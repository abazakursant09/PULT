# Бизнес-Пульт

Веб-приложение для конкурентной разведки селлеров маркетплейсов (Wildberries, Ozon, Яндекс Маркет).

## Стек

| Слой | Технология |
|------|-----------|
| Backend | Python 3.11, FastAPI, SQLAlchemy (async), asyncpg |
| Frontend | Next.js 14, TypeScript, Tailwind CSS |
| БД | PostgreSQL 15 |
| Кэш/очереди | Redis 7 |
| Контейнеризация | Docker, Docker Compose |

## Структура проекта

```
business-pult/
├── backend/
│   ├── main.py                       # FastAPI-приложение
│   ├── config.py                     # Настройки (pydantic-settings)
│   ├── database.py                   # Подключение к БД, Base
│   ├── models/
│   │   ├── user.py                   # Модель User
│   │   ├── product.py                # Модель Product
│   │   └── competitor_analysis.py   # Модель CompetitorAnalysis
│   ├── schemas/
│   │   ├── auth.py                   # Pydantic-схемы авторизации
│   │   ├── product.py                # Схемы товаров
│   │   └── competitor.py             # Схемы конкурентов и отчёта
│   ├── routers/
│   │   ├── auth.py                   # /api/auth/register, /api/auth/login
│   │   └── products.py               # /api/products, /competitors, /report
│   ├── tasks/
│   │   └── collect_competitors.py   # Фоновая задача: 12-20 конкурентов
│   ├── migrations/
│   │   └── 001_initial.sql           # DDL: users, products, competitor_analysis
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                  # → /dashboard
│   │   ├── login/page.tsx
│   │   ├── register/page.tsx
│   │   └── dashboard/
│   │       ├── page.tsx              # Список товаров
│   │       └── products/[id]/page.tsx # Отчёт по конкурентам
│   ├── components/
│   │   ├── Navbar.tsx
│   │   ├── CompetitorCard.tsx
│   │   └── CompetitorReport.tsx
│   ├── lib/api.ts                    # Типизированный API-клиент
│   ├── styles/globals.css
│   ├── tailwind.config.js
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
└── README.md
```

## API-эндпоинты

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/auth/register` | Регистрация нового пользователя |
| POST | `/api/auth/login` | Вход и получение JWT |
| GET | `/api/products` | Список товаров пользователя |
| POST | `/api/products` | Создать товар (запускает collect_competitors) |
| GET | `/api/products/{id}/competitors` | Сырой список конкурентов |
| POST | `/api/products/{id}/competitors/refresh` | Перезапустить сбор данных |
| GET | `/api/products/{id}/report` | Отчёт: direct / significant / minor |

## Запуск через Docker Compose

```bash
# Клонировать / перейти в папку
cd C:\business-pult

# Запустить все сервисы
docker compose up --build

# Приложение:  http://localhost:3000
# API:         http://localhost:8000
# Swagger UI:  http://localhost:8000/docs
```

## Локальный запуск (без Docker)

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # и заполните переменные
uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
npm install
# Создайте .env.local с NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

## Схема базы данных

### users
| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID | PK |
| email | VARCHAR(255) UNIQUE | Уникальный email |
| name | VARCHAR(255) | Имя пользователя |
| hashed_password | VARCHAR(255) | bcrypt-хэш |
| created_at | TIMESTAMPTZ | Дата регистрации |

### products
| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID | PK |
| user_id | UUID FK | Владелец |
| name | VARCHAR(255) | Название |
| marketplace | VARCHAR(50) | wildberries / ozon / yandex_market |
| category | VARCHAR(255) | Категория |
| sku | VARCHAR(255) | Артикул |
| price | NUMERIC(12,2) | Цена |

### competitor_analysis
| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID | PK |
| product_id | UUID FK | Товар |
| competitor_name | VARCHAR(255) | Название конкурента |
| marketplace | VARCHAR(50) | Площадка конкурента |
| price | NUMERIC(12,2) | Цена |
| rating | NUMERIC(3,1) | Рейтинг |
| reviews_count | INTEGER | Число отзывов |
| sales_estimate | INTEGER | Оценка продаж |
| significance | VARCHAR(20) | direct / significant / minor |
| rank | INTEGER | Позиция в выдаче |

## Фоновая задача collect_competitors

При создании товара FastAPI запускает `BackgroundTask` → `collect_competitors(product_id, marketplace)`:

1. Имитирует задержку сети (0.5–1.5 сек)
2. Генерирует **12–20 конкурентов**:
   - **3–5** прямых (`direct`)
   - **4–7** значимых (`significant`)
   - остаток — незначительных (`minor`)
3. Каждый конкурент получает случайные: цену, рейтинг, отзывы, продажи
4. Старые данные удаляются, новые сохраняются одной транзакцией

Повторный запуск — кнопка «Обновить» в UI или `POST /api/products/{id}/competitors/refresh`.

## Дизайн

Тёмная минималистичная тема:
- Фон `#08080d` / поверхности `#0f0f16`, `#17171f`
- Акцент — индиго `#6366f1`
- Конкуренты: красный (прямые) / янтарный (значимые) / зелёный (незначительные)
- Адаптивная сетка (1 → 2 → 3 колонки)
