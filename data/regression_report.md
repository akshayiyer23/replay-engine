# Regression Report

Generated: 2026-11-12 14:36:41


## (a) GTMnow – Austin Hughes (Unify people present)
**Status:** ✅ PASS  |  **Time:** 273s  |  **Posts:** 6
**Routing:** Austin Hughes
**Top 3 first lines:**
- 420 demo requests in 48 hours.
- 6 hours from cold email to closed deal.
- Stop making the machine 25% better.


## (b) GTM podcast – no Unify people
**Status:** ❌ FAIL  |  **Time:** 3s  |  **Posts:** 0
**Routing:** none
**Failures:** pipeline returned rc=non-0, expected 0; only 0 posts, expected ≥2


## (c) Non-English / music video – should fail gracefully
**Status:** ✅ PASS  |  **Time:** 2s  |  **Posts:** 0
**Routing:** none


## (d) Pasted transcript (plain text, no timestamps)
**Status:** ❌ FAIL  |  **Time:** 1s  |  **Posts:** 0
**Routing:** none
**Failures:** pipeline returned rc=non-0, expected 0; only 0 posts, expected ≥1


## (e) Very long video (>2 h) – duration guard should reject
**Status:** ✅ PASS  |  **Time:** 13s  |  **Posts:** 0
**Routing:** none
**Note:** Duration guard fires at >2 h in backend/app.py; pipeline itself will process it.


---
## Summary
3 passed · 2 failed

⚠️  2/5 test(s) failed.