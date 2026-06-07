# Strategy Agent
class StrategyAgent:
    def __init__(self, blackboard=None):
        self.blackboard = blackboard or {}
    def run(self, state):
        return state
