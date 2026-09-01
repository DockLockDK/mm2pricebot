# Деплой webapp/server.py на свой VPS

Это постоянно работающий процесс (бот + API мини-приложения + фоновая
проверка цен). Нужен Linux-сервер с Python 3.10+ и настоящим https-доменом
(без https кнопка "Открыть каталог" в Telegram не заработает — это
требование самого Telegram Web Apps, localhost/http не подходят).

## 1. Код и зависимости

```bash
git clone https://github.com/DockLockDK/mm2pricebot.git
cd mm2pricebot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Домен и https (обратный прокси)

`webapp/server.py` сам поднимает только обычный http на порту из `PORT`
(по умолчанию 8000). Нужен nginx (или Caddy — он сам получает
сертификат, тогда шаг с certbot не нужен) перед ним, который отдаёт https
и проксирует на этот порт.

Пример для nginx + certbot (замени `mm2.example.com` на свой домен,
DNS-запись A на этот сервер должна быть настроена заранее):

```nginx
server {
    listen 80;
    server_name mm2.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo apt install nginx certbot python3-certbot-nginx
sudo cp nginx.conf /etc/nginx/sites-available/mm2
sudo ln -s /etc/nginx/sites-available/mm2 /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d mm2.example.com   # выпускает сертификат и сам донастраивает конфиг на https
```

## 3. Переменные окружения

- `TELEGRAM_BOT_TOKEN` — токен бота у @BotFather (тот же, что уже используется)
- `TELEGRAM_CHAT_ID` — куда слать автоматические push-уведомления
- `WEBAPP_URL` — **обязательно**, публичный https-адрес из шага 2,
  например `https://mm2.example.com` (без слэша на конце)
- `CHECK_INTERVAL_SEC` — как часто проверять цены, сек (по умолчанию 300)
- `PORT` — порт, на котором слушает uvicorn (по умолчанию 8000, должен
  совпадать с `proxy_pass` в nginx-конфиге)

## 4. Постоянный запуск через systemd

`/etc/systemd/system/mm2bot.service`:

```ini
[Unit]
Description=MM2 price bot + mini app
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/mm2pricebot
Environment=TELEGRAM_BOT_TOKEN=xxxxx
Environment=TELEGRAM_CHAT_ID=xxxxx
Environment=WEBAPP_URL=https://mm2.example.com
ExecStart=/home/YOUR_USER/mm2pricebot/venv/bin/python webapp/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mm2bot
sudo systemctl status mm2bot     # должно быть "active (running)"
journalctl -u mm2bot -f          # логи в реальном времени
```

## 5. Проверка

Напиши боту `/start` в Telegram — должна прийти кнопка
"🛒 Открыть каталог", которая открывает мини-приложение прямо внутри
Telegram.

## 6. Отключить старый GitHub Actions cron

Как только бот на сервере стабильно работает (шлёт те же уведомления),
отключи workflow `mm2-tracker.yml` в GitHub Actions — иначе будешь
получать уведомления дважды: и от cron, и от постоянного бота. Просто
скажи Claude — отключит сам.
