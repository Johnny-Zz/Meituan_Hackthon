# LangGraph-style offline flow skeleton.
NODES = ["LoadCase","ExtractFeatures","SceneRoute","StrategyDispatch","Evaluate","NoRegression","Distill","BackupOrRollback"]
EDGES = list(zip(NODES, NODES[1:]))
