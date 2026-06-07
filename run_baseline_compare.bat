@echo off
echo Active solver:
python local_test.py submission/solver.py cases\large_seed301.txt
echo.
echo Greedy baseline:
python local_test.py baselines\example_solution.py cases\large_seed301.txt
pause
