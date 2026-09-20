from app.backtest.statistics import Statistics


class Report:

    @staticmethod
    def print(trades, start_balance, end_balance):

        stats = Statistics(
            trades,
            start_balance,
            end_balance
        )

        print("=" * 40)
        print("BACKTEST REPORT")
        print("=" * 40)

        print(f"Start Balance : {stats.start_balance:.2f}$")
        print(f"End Balance   : {stats.end_balance:.2f}$")
        print(f"Profit        : {stats.profit:.2f}$")
        print(f"Return        : {stats.profit_percent:.2f}%")
        print()

        print(f"Trades        : {len(stats.trades)}")
        print(f"Wins          : {stats.wins}")
        print(f"Losses        : {stats.losses}")
        print(f"Win Rate      : {stats.win_rate:.2f}%")