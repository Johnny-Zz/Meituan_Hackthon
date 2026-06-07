@echo off
cd /d %~dp0
python tools\score_feedback_agent.py --image memory\score_feedback\your_score_screenshot.png --source bat_demo --notes "online score screenshot"
pause
