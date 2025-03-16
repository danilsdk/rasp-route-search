# Импортируем необходимые библиотеки
from queue import PriorityQueue
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import random
import requests
import requests_cache
import math

# Параметры API и кэширование в файл rasp_cache.sqlite
api_key = "0a272229-f9fd-4f08-a4cd-9083e853b7ec"
requests_cache.install_cache('rasp_cache', backend='sqlite', expire_after=3600)  # кэш хранится час (3600 секунд)
session = requests.Session()


# Используем декоратор lru_cache для кэширования
@lru_cache(maxsize=100)
def get_city_code(name: str) -> str:
    """Получает код города по его названию на русском."""

    url = "https://api.rasp.yandex.net/v3.0/stations_list/"
    params = {
        "apikey": api_key,
        "lang": "ru_RU",
        "format": "json"
    }

    try:
        response = session.get(url, params=params)
        response.raise_for_status()  # проверяем, что запрос прошел успешно
    except Exception as e:
        print(f"Ошибка запроса для {name}: {e}")
        return ''

    data = response.json()
    for country in data.get("countries", []):
        for region in country.get("regions", []):
            for settlement in region.get("settlements", []):
                if name in settlement.get("title", ""):
                    return settlement.get("codes", {}).get("yandex_code", "")
    print(f"Город {name} не найден")
    return ''


def get_routes(from_city: str, to_city: str, date: str) -> list:
    """Запрашивает маршруты между двумя городами на указанную дату."""

    city1, city2 = get_city_code(from_city), get_city_code(to_city)
    if not city1 or not city2:
        print("Город не найден")
        return []

    url = "https://api.rasp.yandex.net/v3.0/search/"
    params = {
        "apikey": api_key,
        "from": city1,
        "to": city2,
        "transfers": False,
        "date": date,
        "format": "json"
    }

    try:
        response = session.get(url, params=params)
        response.raise_for_status()
    except Exception as e:
        print(f"Ошибка запроса для {from_city} -> {to_city}: {e}")
        return []

    return response.json().get("segments", [])


def extract_route_info(route) -> list:
    """Обрабатывает один маршрут и возвращает его в виде списка этапов."""

    result = []
    if route.get("has_transfers", False) and "details" in route:
        for detail in route["details"]:
            if detail.get("is_transfer", False):
                transfer_point = detail.get("transfer_point", {}).get("title")
                transfer_duration = detail.get("duration", 0)
                hours, minutes = transfer_duration // 3600, (transfer_duration % 3600) // 60
                result.append({
                    "type": "transfer",
                    "location": transfer_point,
                    "dur_hours": hours,
                    "dur_minutes": minutes
                })
            else:
                departure = datetime.strptime(detail["departure"], "%Y-%m-%dT%H:%M:%S%z").strftime("%H:%M")
                arrival = datetime.strptime(detail["arrival"], "%Y-%m-%dT%H:%M:%S%z").strftime("%H:%M")
                from_city = detail["from"]["title"]
                to_city = detail["to"]["title"]
                transport_type = detail["thread"]["transport_type"]
                duration = detail.get("duration", 0)
                hours, minutes = duration // 3600, (duration % 3600) // 60
                result.append({
                    "type": transport_type,
                    "from": from_city,
                    "to": to_city,
                    "departure": departure,
                    "arrival": arrival,
                    "dur_hours": hours,
                    "dur_minutes": minutes
                })
    else:
        from_city = route["from"]["title"]
        to_city = route["to"]["title"]
        departure = datetime.strptime(route["departure"], "%Y-%m-%dT%H:%M:%S%z").strftime("%H:%M")
        arrival = datetime.strptime(route["arrival"], "%Y-%m-%dT%H:%M:%S%z").strftime("%H:%M")
        transport_type = route["thread"]["transport_type"]
        duration = route.get("duration", 0)
        hours, minutes = duration // 3600, (duration % 3600) // 60
        result.append({
            "type": transport_type,
            "from": from_city,
            "to": to_city,
            "departure": departure,
            "arrival": arrival,
            "dur_hours": hours,
            "dur_minutes": minutes
        })

    return result


def fetch_routes_pair(city1: str, city2: str, date: datetime) -> tuple:
    """Функция для упрощения запросов к API. Делает два запроса на получение маршрутов и объединяет их в один список."""

    date2 = date + timedelta(days=1)
    routes1 = get_routes(city1, city2, date.strftime("%Y-%m-%d"))
    routes2 = get_routes(city1, city2, date2.strftime("%Y-%m-%d"))
    return city1, city2, routes1 + routes2


def build_routes_list(cities: list, date: str) -> list:
    """ Функция для построения списка маршрутов между городами."""

    start_time = datetime.strptime(date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    routes_list = []
    pairs = []

    for i, city1 in enumerate(cities):
        for city2 in cities[i + 1:]:
            pairs.append((city1, city2))

    # Создаем список маршрутов между городами с использованием многопоточности
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(fetch_routes_pair, pair[0], pair[1], start_time): pair
            for pair in pairs
        }

        for future in as_completed(futures):
            city1, city2, routes = future.result()
            for route in routes:
                if route.get("has_transfers", False):
                    continue
                if route.get("thread", {}).get("transport_type") != "plane":
                    continue
                processed_route = extract_route_info(route)
                if not processed_route:
                    continue
                step = processed_route[0]
                total_minutes = step.get("dur_hours", 0) * 60 + step.get("dur_minutes", 0)
                total_hours = total_minutes // 60
                remaining_minutes = total_minutes % 60
                total_duration = f"{total_hours} ч {remaining_minutes} м"
                dep_dt = datetime.fromisoformat(route["departure"]).astimezone(timezone.utc)
                arr_dt = datetime.fromisoformat(route["arrival"]).astimezone(timezone.utc)
                departure_time = dep_dt.strftime("%Y-%m-%d %H:%M:%S")
                arrival_time = arr_dt.strftime("%Y-%m-%d %H:%M:%S")

                if dep_dt < start_time:
                    continue

                # Генерируем случайную цену
                rnd = random.Random()
                price = round(50 + total_minutes * 0.8 + rnd.uniform(-10, 10), 2)

                route_data = {
                    "from": city1,
                    "to": city2,
                    "total_duration": total_duration,
                    "departure_time": departure_time,
                    "arrival_time": arrival_time,
                    "price": price
                }
                routes_list.append(route_data)

    return routes_list


def get_city_stations_codes(city: str) -> list:
    """ Функция для получения кодов станций для города."""

    url = "https://api.rasp.yandex.net/v3.0/stations_list/"
    params = {
        "apikey": api_key,
        "lang": "ru_RU",
        "format": "json"
    }

    try:
        response = session.get(url, params=params)
        response.raise_for_status()
    except Exception as e:
        print(f"Ошибка запроса для {city}: {e}")
        return []

    data = response.json()
    codes = []
    for country in data.get("countries", []):
        for region in country.get("regions", []):
            for settlement in region.get("settlements", []):
                if city in settlement.get("title", ""):
                    for station in settlement.get("stations", []):
                        station_code = station.get("codes", {}).get("yandex_code", "")
                        if station_code and station_code not in codes and station.get(
                                "transport_type") == "plane" or station.get("transport_type") == "Самолёт":
                            codes.append(station_code)

    return codes


def get_routes_from_stations(station_codes: list, date: str) -> list:
    """Функция для получения маршрутов из списка станций."""

    all_routes = []
    url = "https://api.rasp.yandex.net/v3.0/schedule/"
    for station in station_codes:
        params = {
            "apikey": api_key,
            "station": station,
            "date": date,
            "format": "json"
        }

        try:
            response = session.get(url, params=params)
            response.raise_for_status()

        except Exception:
            continue

        data = response.json()
        if data.get("schedule"):
            for entry in data["schedule"]:
                to = " ".join(entry["thread"]["title"].split(" — ")[1:])
                departure = entry["departure"]
                all_routes.append({"from": station, "to": to, "departure": departure})

    return all_routes


def get_reachable_cities(city: str, dt_str: str) -> list:
    """ Функция для получения доступных городов для заданного города и времени."""

    start_time = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    station_codes = get_city_stations_codes(city)

    if not station_codes:
        print(f"Станции для города {city} не найдены.")
        return []

    dates_to_query = {start_time.strftime("%Y-%m-%d"), (start_time + timedelta(days=1)).strftime("%Y-%m-%d")}
    all_routes = []

    for d in dates_to_query:
        routes = get_routes_from_stations(station_codes, d)
        all_routes.extend(routes)

    reachable = []
    for route in all_routes:
        dep_str = route.get("departure")
        if not dep_str:
            continue

        try:
            dep_dt = datetime.fromisoformat(dep_str).astimezone(timezone.utc)
        except Exception as e:
            print(f"Ошибка парсинга даты: {e}")
            continue

        if dep_dt > start_time:
            to_info = route.get("to")
            dep_formatted = dep_dt.strftime("%Y-%m-%d %H:%M:%S")
            reachable.append({
                "destination": to_info,
                "departure_time": dep_formatted,
            })

    unique = {}
    for item in reachable:
        key = (item["destination"], item["departure_time"])
        unique[key] = item

    result = list(set([elem["destination"] for elem in unique.values()]))
    return result


def make_datetime(str_dt) -> datetime:
    """ Функция для преобразования строки в объект datetime. """
    return datetime.strptime(str_dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def find_priority(route: dict, city_to: str, priority_name: str = "price") -> int:
    """ Функция для вычисления приоритета маршрута. """

    priority = int((time_to_minutes(route['total_duration']) + route['price'] +
                    (distance_between_cities(route['to'], city_to) / 10))) + 200

    # Города с самыми большими аэропортами и чаще всего встречающиеся в маршрутах в качестве пересадок
    cities = [
        "Стамбул", "Лондон", "Париж", "Дубай", "Амстердам",
        "Франкфурт", "Нью-Йорк", "Доха", "Гонконг", "Сингапур",
        "Токио", "Сеул", "Мадрид", "Чикаго", "Лос-Анджелес",
        "Пекин", "Шанхай", "Мюнхен", "Сан-Франциско",
        "Даллас", "Сидней", "Куала-Лумпур", "Бангкок",
        "Цюрих", "Вена", "Милан", "Копенгаген", "Хельсинки", "Москва",
        "Санкт-Петербург", "Екатеринбург", "Казань"
    ]
    if route["to"] in cities:
        priority -= 300

    if priority_name == "default":
        return priority
    if priority_name == "price":
        priority += 10 * route['price']
    if priority_name == "total_duration":
        priority += 10 * time_to_minutes(route['total_duration'])
    if priority_name == "number_of_transfers":
        priority += 200

    return priority


def time_to_minutes(time_str) -> int:
    """ Функция для преобразования времени в минуты. """

    if not time_str:
        return 0

    parts = time_str.split()
    hours = int(float(parts[0]))
    minutes = int(float(parts[2]))

    return hours * 60 + minutes


@lru_cache(maxsize=256)
def cached_build_routes_list(cities_tuple, date: str) -> list:
    """ Функция для кэширования списка маршрутов. """

    # cities_tuple – кортеж из 2 городов
    return build_routes_list(list(cities_tuple), date)


@lru_cache(maxsize=256)
def cached_get_reachable_cities(city: str, dt_str: str) -> tuple:
    """ Функция для кэширования списка доступных городов. """

    # Возвращает кортеж городов, чтобы результат был хэшируемым
    return tuple(get_reachable_cities(city, dt_str))


def find_route(city_from: str, city_to: str, departure_date_str: str, minimum_waiting_time_for_transfer: int,
               count_find_routes: int, priority: str = "price") -> list:
    """ Функция для поиска маршрута. """

    departure_date = make_datetime(departure_date_str)
    print(f"DEBUG: Запуск поиска маршрута из {city_from} в {city_to} с {departure_date_str}")

    frontier = PriorityQueue()
    # Храним: (накопленный приоритет, название города, время прибытия как datetime)
    frontier.put((0, city_from, departure_date))
    came_from = {city_from: None}
    cost_so_far = {city_from: 0}
    result_routes = []

    while not frontier.empty():
        current_priority, current_city, current_arrival = frontier.get()
        if current_priority > 3000:
            return None
        print(
            f"DEBUG: Обрабатываем {current_city} с приоритетом {current_priority} и временем прибытия {current_arrival}")

        min_departure_time_dt = current_arrival + timedelta(hours=3)
        min_departure_time_str = min_departure_time_dt.strftime("%Y-%m-%d %H:%M:%S")
        print(f"DEBUG: Минимальное время отправления для {current_city}: {min_departure_time_str}")

        reachable_cities = cached_get_reachable_cities(current_city, min_departure_time_str)
        print(f"DEBUG: Из города {current_city} доступны города: {reachable_cities}")

        if city_to in reachable_cities and len(
                cached_build_routes_list((current_city, city_to), min_departure_time_str)) > 0:
            routes = cached_build_routes_list((current_city, city_to), min_departure_time_str)
            if frontier.empty():
                result = []
                for route in routes:
                    result.append([route])
                result.sort(key=lambda x: find_priority(x[0], city_to, priority))
                return result[:count_find_routes]
            for route in routes:
                new_cost = current_priority + find_priority(route, city_to, priority)
                if city_to not in cost_so_far or new_cost < cost_so_far[city_to]:
                    cost_so_far[city_to] = new_cost
                    came_from[city_to] = route
                    print(f"DEBUG: Обновление: {current_city} -> {city_to} с новым приоритетом {new_cost}")
            print("DEBUG: Достигнут пункт назначения. Восстанавливаем маршрут...")
            del cost_so_far[city_to]
            current_city = city_to
            path = []
            while came_from[current_city] is not None:
                path.append(came_from[current_city])
                current_city = came_from[current_city]["from"]
            path.reverse()
            result_routes.append(path)
            if len(result_routes) == count_find_routes:
                print(f"DEBUG: Маршруты найдены: {result_routes}")
                return result_routes
            continue

        for next_city in reachable_cities:
            routes = cached_build_routes_list((current_city, next_city), min_departure_time_str)
            print(f"DEBUG: Найдено {len(routes)} маршрутов из {current_city} в {next_city}")
            for route in routes:
                route_departure = make_datetime(route["departure_time"])
                route_arrival = make_datetime(route["arrival_time"])
                if route_departure - min_departure_time_dt > timedelta(hours=minimum_waiting_time_for_transfer):
                    continue
                new_cost = (current_priority + find_priority(route, city_to, priority) +
                            int((route_departure - min_departure_time_dt).total_seconds() // 60))

                if next_city not in cost_so_far or new_cost < cost_so_far[next_city]:
                    cost_so_far[next_city] = new_cost
                    frontier.put((new_cost, next_city, route_arrival))
                    came_from[next_city] = route
                    print(f"DEBUG: Обновление: {current_city} -> {next_city} с новым приоритетом {new_cost}")

    print("DEBUG: Маршрут не найден.")
    return None


@lru_cache(maxsize=1)
def get_all_stations_data() -> dict:
    """
    Получает и кэширует данные API, содержащие список всех станций и городов.
    Ответ может быть большим, поэтому кэшируется один раз.
    """
    url = "https://api.rasp.yandex.net/v3.0/stations_list/"
    params = {"apikey": api_key, "lang": "ru_RU", "format": "json"}
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
    except Exception as e:
        print(f"Ошибка запроса к stations_list: {e}")
        return dict()

    return response.json()


@lru_cache(maxsize=256)
def get_city_station_coordinates(city_name: str) -> list:
    """
    Ищет станции в поселениях, название которых содержит city_name.
    Для каждой найденной станции проверяет, что transport_type равен "plane" или "Самолет".
    Возвращает список кортежей (latitude, longitude) для всех подходящих станций.
    """

    data = get_all_stations_data()
    if data is None:
        return []
    station_coords = []
    for country in data.get("countries", []):
        for region in country.get("regions", []):
            for settlement in region.get("settlements", []):
                # Если имя города встречается в названии поселения
                if city_name.strip().lower() in settlement.get("title", "").strip().lower():
                    # Проходим по всем станциям поселения
                    for station in settlement.get("stations", []):
                        transport = station.get("transport_type", "").lower()
                        if transport in ("plane", "самолет"):
                            try:
                                lat = float(station.get("latitude"))
                                lon = float(station.get("longitude"))
                                station_coords.append((lat, lon))
                            except (TypeError, ValueError):
                                continue
    return station_coords


def haversine(lat1, lon1, lat2, lon2) -> float:
    """
    Вычисляет расстояние между двумя точками на Земле по формуле Хаверсайна.
    Возвращает расстояние в километрах.
    """

    R = 6371.0  # Радиус Земли в км
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def distance_between_cities(city1: str, city2: str) -> float:
    """
    Принимает названия двух городов, собирает координаты станций для каждого города и возвращает
    минимальное расстояние между любой парой станций (в километрах).
    Если для одного из городов не найдены подходящие станции, генерирует исключение.
    """

    coords1 = get_city_station_coordinates(city1)
    coords2 = get_city_station_coordinates(city2)
    if not coords1 or not coords2:
        return 10000000000  # Большое расстояние в случае отсутствия координат
    min_distance = float('inf')
    for (lat1, lon1) in coords1:
        for (lat2, lon2) in coords2:
            d = haversine(lat1, lon1, lat2, lon2)
            if d < min_distance:
                min_distance = d
    return min_distance


def routes_info(route_list: list) -> None:
    """ Выводит информацию о маршрутах """

    print(f"\nИтого маршрутов {route_list[0][0]['from']} -> {route_list[0][-1]['to']} - {len(route_list)}")
    for i in range(len(route_list)):
        route = route_list[i]

        total_price = 0
        print(f"\nМаршрут {i + 1}:")
        for i in range(len(route)):
            print(
                f"    {i + 1}. {route[i]['from']} -> {route[i]['to']}, {datetime.strptime(route[i]['departure_time'], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')} ({datetime.strptime(route[i]['departure_time'], '%Y-%m-%d %H:%M:%S').strftime('%H:%M')} - {datetime.strptime(route[i]['arrival_time'], '%Y-%m-%d %H:%M:%S').strftime('%H:%M')})")

            if i != 0:
                form = '%Y-%m-%d %H:%M:%S'
                dt1 = datetime.strptime(route[i - 1]['arrival_time'], form)
                dt2 = datetime.strptime(route[i]['departure_time'], form)

                transfer_info = f", Длительность пересадки: {round((dt2 - dt1).total_seconds() / 3600, 2)} часов"
            else:
                transfer_info = ""

            print(f"    Цена: {route[i]['price']}, Время в пути: {route[i]['total_duration']}" + transfer_info)
            total_price += route[i]['price']

        fmt = '%Y-%m-%d %H:%M:%S'
        dt1 = datetime.strptime(route[0]['departure_time'], fmt)
        dt2 = datetime.strptime(route[-1]['arrival_time'], fmt)

        total_duration = round((dt2 - dt1).total_seconds() / 3600, 2)
        total_price = round(total_price, 2)

        print(
            f"Итоговая цена: {total_price}, Итоговое время в пути: {total_duration} часов, Пересадок: {len(route) - 1}")


if __name__ == "__main__":
    # Пример использования - нахождение маршрута между городами
    date = "2025-03-20 18:00:00"

    # 12 - максимальное время ожидания для пересадки (в часах)
    routes = find_route("Екатеринбург", "Москва", date, 12, 3, "duration")
    routes_info(routes)
