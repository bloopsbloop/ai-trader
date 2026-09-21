from app.backtest.statistics import Statistics


class Report:
    @staticmethod
    def render(stats):
        profit_factor = "∞" if stats.profit_factor == float("inf") else f"{stats.profit_factor:.2f}"
        return "\n".join([
            "=" * 48, "BACKTEST REPORT", "=" * 48,
            f"Start Balance : {stats.start_balance:,.2f}$",
            f"End Balance   : {stats.end_balance:,.2f}$",
            f"Profit        : {stats.profit:+,.2f}$ ({stats.profit_percent:+.2f}%)",
            "-" * 48, f"Trades        : {len(stats.trades)}",
            f"Wins / Losses : {stats.wins} / {stats.losses}", f"Win Rate      : {stats.win_rate:.2f}%",
            f"Profit Factor : {profit_factor}", f"Average Win   : {stats.average_win:,.2f}$",
            f"Average Loss  : {stats.average_loss:,.2f}$", f"Expectancy    : {stats.expectancy:+,.2f}$",
            f"Max Drawdown  : {stats.max_drawdown:.2f}%", f"Sharpe Ratio  : {stats.sharpe_ratio:.2f}", "=" * 48,
        ])

    @classmethod
    def print(cls, stats, start_balance=None, end_balance=None, equity_curve=None):
        if not isinstance(stats, Statistics):
            stats = Statistics(stats, start_balance, end_balance, equity_curve)
        report = cls.render(stats)
        print(report)
        return report
