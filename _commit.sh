cd /c/Users/XX/WorkBuddy/2026-08-01-16-23-52/exam-notice-workbench
echo '--- diff stat ---'
git diff --stat build_dashboard.py index.html
echo '--- add ---'
git add build_dashboard.py index.html
echo '--- commit ---'
git commit -m "style: 删侧边栏考试信息父项 + 放大剩余两项 (15px) + 收窄侧栏 215px"
echo '--- push ---'
for i in 1 2 3 4; do
  sleep 8
  if git push origin main 2>&1 | tail -3; then
    echo 'push done'
    break
  fi
done
