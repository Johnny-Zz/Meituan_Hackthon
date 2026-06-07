@echo off
cd /d %~dp0
python tools\autonomous_patch_agent.py --source bat --objective "manual autonomous solver.py patch with no-regression gate"
pause
