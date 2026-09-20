class RiskManager:

    def __init__(self, risk_percent=2):
        self.risk_percent = risk_percent

    def calculate_position_size(self, balance):

        return balance * (self.risk_percent / 100)