# StockPilotAI

StockPilotAI is a local-first web application that combines timestamped market data,
deterministic technical indicators, and structured OpenAI analysis to suggest one of
four research actions: **Buy**, **Hold**, **Sell**, or **Watch**.

The MVP runs on your laptop, stores analysis history in a local SQLite file, and is
organized so the database, market-data provider, AI engine, authentication, and
background jobs can be replaced or extended for a future cloud product.

> StockPilotAI provides educational research, not personalized financial advice.
> Market data and AI output can be incomplete or wrong. Verify information independently.

## MVP features

- Local FastAPI web interface at `http://127.0.0.1:8000`
- Stock ticker, investment horizon, ownership status, and model selection
- One year of price history through a replaceable Yahoo chart adapter
- SMA 20/50/200, one/three/twelve-month returns, RSI 14, and volatility
- Structured OpenAI Responses API output validated by Pydantic
- Ownership-aware `BUY`, `HOLD`, `SELL`, and `WATCH` guardrails
- Observable future-entry and thesis-invalidation conditions
- Local SQLite analysis history
- JSON API, health endpoint, migrations, tests, and Docker support

Not included yet: email, scheduled reports, news or sentiment, fundamentals, user
accounts, portfolios, multi-agent analysis, cloud infrastructure, or trading.

## Requirements

- Python 3.11 or newer
- An OpenAI API key exported as `OPENAI_API_KEY` or saved in a local `.env`
- Internet access for market data and OpenAI

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

Edit `.env` and replace the placeholder API key. If `OPENAI_API_KEY` is already
available in your shell, you may remove the placeholder line instead.

Start the app:

```bash
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The database is created at `data/stockpilot.db`. It persists after the local server
stops and is ignored by Git.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | none | Server-side OpenAI credential |
| `OPENAI_DEFAULT_MODEL` | `gpt-5-mini` | Preselected UI model |
| `OPENAI_ALLOWED_MODELS` | `gpt-5-mini,gpt-4.1-mini,gpt-4o-mini` | Comma-separated model allowlist |
| `OPENAI_TIMEOUT_SECONDS` | `60` | OpenAI request timeout |
| `DATABASE_URL` | `sqlite:///data/stockpilot.db` | SQLite locally; PostgreSQL later |
| `MARKET_DATA_TIMEOUT_SECONDS` | `20` | Market-data request timeout |

Model availability depends on the OpenAI project associated with your API key. Edit
the allowlist when you want to add or remove selectable models.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Local analysis UI |
| `POST` | `/analyze` | Submit the HTML form |
| `GET` | `/analyses/{id}` | Open a saved result |
| `GET` | `/api/v1/models` | Read configured model choices |
| `POST` | `/api/v1/analyses` | Create a JSON analysis |
| `GET` | `/api/v1/analyses` | List local history |
| `GET` | `/api/v1/analyses/{id}` | Retrieve one analysis |
| `GET` | `/health` | Health check |

Example JSON request:

```json
{
  "symbol": "AAPL",
  "horizon": "long_term",
  "owns_stock": false,
  "model": "gpt-5-mini"
}
```

## Project structure

```text
StockPilotAI/
├── app/
│   ├── api/routes/               # HTML and JSON routes
│   ├── core/                     # Settings, errors, logging
│   ├── domain/                   # Typed requests, results, market snapshots
│   ├── providers/
│   │   ├── llm/                  # OpenAI structured analysis adapter
│   │   └── market_data/          # Replaceable market-data adapter
│   ├── repositories/             # SQLAlchemy persistence
│   ├── services/                 # Workflow and indicator calculations
│   ├── static/                   # CSS and JavaScript
│   ├── templates/                # Server-rendered UI
│   └── main.py                   # Application assembly
├── migrations/                   # Alembic database migrations
├── tests/                        # Unit and integration tests
├── data/                         # Ignored local database files
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Database migrations

The application creates the initial table automatically for a convenient local start.
Alembic is included for controlled schema changes:

```bash
alembic upgrade head
```

For a cloud release, change `DATABASE_URL` to PostgreSQL and run migrations during
deployment. Do not put SQLite on an ephemeral container filesystem.

## Tests

Tests use deterministic fake providers and do not spend OpenAI credits:

```bash
pytest
```

## Current data limitations

The MVP uses Yahoo chart data because it needs no second API key. This is convenient
for personal prototyping but is not a contractual or licensed production feed. The
adapter is deliberately isolated behind `MarketDataProvider`; replace it with a
licensed provider before a public release.

The current analysis uses price-derived indicators only. It does not include company
financial statements, valuation, earnings, filings, news, sentiment, or macroeconomic
data. The prompt requires the model to disclose those limitations and avoid inventing
missing facts.

## Future path

- PostgreSQL and per-user ownership of saved analyses
- Authentication and authorization
- SQS-style background jobs for long-running multi-agent analysis
- Independent technical, fundamental, risk, and contrarian agents plus a judge
- Scheduled watchlists and email reports
- Licensed market/fundamental data
- Rate limiting, usage budgets, audit logs, and production monitoring

## License

MIT
