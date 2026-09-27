# StockPilotAI

An intelligent AI-powered stock analysis and recommendation system that delivers personalized daily updates directly to your email inbox.

## Overview

StockPilotAI leverages artificial intelligence to analyze stocks you're following and provides actionable recommendations. Using multiple analysis strategies and technical indicators, the application evaluates market trends and sends you daily updates with clear recommendations: **Hold**, **Buy**, **Sell**, or **Watch**.

## Features

- **AI-Powered Analysis**: Advanced machine learning algorithms analyze stock performance and market patterns
- **Multi-Strategy Approach**: Combines multiple analysis techniques including:
  - Technical analysis
  - Sentiment analysis
  - Market trend analysis
  - Historical pattern recognition
- **Daily Email Updates**: Receive personalized stock analysis and recommendations directly in your inbox
- **Smart Recommendations**: Get clear action items:
  - **Buy**: Strong indicators suggest purchasing
  - **Sell**: Signals indicate it's time to exit
  - **Hold**: Current position is optimal
  - **Watch**: Monitor for potential opportunities
- **Portfolio Tracking**: Monitor multiple stocks simultaneously

## Getting Started

### Prerequisites

- Python 3.8 or higher
- Email account configured for sending notifications
- API keys for market data (e.g., Alpha Vantage, Finnhub, or similar)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/BruceInAIEra/StockPilotAI.git
   cd StockPilotAI
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure your environment variables:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your API keys and email settings

### Configuration

Update the configuration file with:
- Stock symbols to monitor
- Your email address for daily updates
- Email service credentials (SMTP settings)
- Market data API keys
- Analysis preferences and thresholds

### Usage

Run the daily analysis:
```bash
python main.py
```

Schedule daily updates using cron (Linux/Mac) or Task Scheduler (Windows):
```bash
# Run every morning at 9 AM
0 9 * * * /usr/bin/python3 /path/to/StockPilotAI/main.py
```

## How It Works

1. **Data Collection**: Fetches current market data and historical prices for monitored stocks
2. **Analysis**: Applies multiple AI strategies to analyze trends, patterns, and market sentiment
3. **Recommendation Engine**: Combines analysis results to generate buy/sell/hold/watch signals
4. **Report Generation**: Creates a comprehensive analysis report
5. **Email Delivery**: Sends formatted recommendations to your email inbox

## Project Structure

```
StockPilotAI/
├── main.py                 # Entry point
├── requirements.txt        # Python dependencies
├── .env.example           # Environment configuration template
├── config/                # Configuration files
├── ai/                    # AI models and analysis engines
├── analysis/              # Analysis strategies
├── data/                  # Data collection and processing
├── notifications/         # Email notification system
└── README.md             # This file
```

## Technologies

- **Python**: Core programming language
- **AI/ML**: Machine learning libraries for predictive analysis
- **APIs**: Market data integration
- **Email**: SMTP for notification delivery

## Recommendations Explained

- **🟢 BUY**: Technical indicators and AI analysis show strong bullish signals
- **🔴 SELL**: Multiple indicators suggest downward pressure; consider exiting
- **🟡 HOLD**: Current position is stable; maintain existing holdings
- **🔵 WATCH**: Stock shows potential but requires monitoring before action

## Configuration Example

Create a `.env` file with the following:
```
# Email Configuration
EMAIL_SENDER=your-email@gmail.com
EMAIL_PASSWORD=your-app-password
EMAIL_RECIPIENT=recipient@example.com
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587

# Market Data API
MARKET_DATA_API_KEY=your_api_key_here
MARKET_DATA_PROVIDER=alpha_vantage

# Stocks to Monitor
STOCKS=AAPL,MSFT,GOOGL,TSLA

# Analysis Settings
ANALYSIS_THRESHOLD=0.65
UPDATE_TIME=09:00
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request with improvements, bug fixes, or new analysis strategies.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

**Important**: StockPilotAI provides AI-generated analysis and recommendations for informational purposes only. It should not be considered professional financial advice. Always conduct your own research and consult with a qualified financial advisor before making investment decisions. Past performance does not guarantee future results. Invest responsibly and never risk more than you can afford to lose.

## Contact & Support

For issues, questions, or suggestions, please open an issue on GitHub or contact the maintainers.

---

**Start your intelligent stock analysis journey with StockPilotAI today!**