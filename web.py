from flask import Flask, request, render_template
from main import find_route
from datetime import datetime

app = Flask(__name__)


def format_routes(route_list: list) -> list:
    """Форматирует маршруты в читаемый вид для шаблона"""

    if not route_list:
        return []

    formatted_routes = []
    for idx, route in enumerate(route_list, start=1):
        total_price = round(sum(seg['price'] for seg in route), 3)
        fmt = '%Y-%m-%d %H:%M:%S'
        total_duration = round(
            (datetime.strptime(route[-1]['arrival_time'], fmt) - datetime.strptime(route[0]['departure_time'],
                                                                                   fmt)).total_seconds() / 3600, 2
        )

        route_str = f"\nМаршрут {idx}:\n"
        for i, seg in enumerate(route):
            departure = datetime.strptime(seg['departure_time'], fmt)
            arrival = datetime.strptime(seg['arrival_time'], fmt)
            transfer_info = ""
            if i > 0:
                prev_arrival = datetime.strptime(route[i - 1]['arrival_time'], fmt)
                transfer_time = round((departure - prev_arrival).total_seconds() / 3600, 2)
                transfer_info = f", Длительность пересадки: {transfer_time} часов"

            route_str += f"    {i + 1}. {seg['from']} -> {seg['to']}, {departure.strftime('%d.%m.%Y')} ({departure.strftime('%H:%M')} - {arrival.strftime('%H:%M')})\n"
            route_str += f"    Цена: {seg['price']}, Время в пути: {seg['total_duration']}{transfer_info}\n"

        route_str += f"Итоговая цена: {total_price}, Итоговое время в пути: {total_duration} часов, Пересадок: {len(route) - 1}\n"
        formatted_routes.append(route_str)

    return formatted_routes


@app.route('/', methods=['GET', 'POST'])
def index():
    found_routes = None
    if request.method == 'POST':
        city_from = request.form.get('city_from')
        city_to = request.form.get('city_to')
        date = request.form.get('date')
        waiting_time = int(request.form.get('waiting_time', 12))
        count_routes = int(request.form.get('count_routes', 3))
        priority = request.form.get('priority', 'price')
        print(priority)

        # Получаем маршруты
        routes = find_route(city_from, city_to, date, waiting_time, count_routes, priority)
        found_routes = format_routes(routes)

    return render_template('index.html', found_routes=found_routes)


if __name__ == '__main__':
    app.run(debug=True)
