# 哪些源「关机也有」、哪些「必须开机」

- 对照基础：云端（GitHub Actions 海外出口）2026-10-26 17:08:47 实测 72 个轻量源；本机（中国大陆出口）2026-10-26 17:08:45 实测 72 个。
- 结论汇总：轻量源中 **两边都抓得到 43 个**、**只有本机能抓 26 个**、两边都抓不到 3 个。

## A. 关机也有（云端实测有产出）

| 源 | 云端条数 | 本机条数 |
|---|---|---|
| 三明-事业单位招聘 | 3 | 3 |
| 上海-事业单位招聘 | 264 | 264 |
| 上海-事业单位统考公告 | 6 | 6 |
| 上海-奉贤区-事业单位 | 2 | 2 |
| 上海-奉贤区-招聘搜索 | 10 | 10 |
| 上海-宝山区-招聘搜索 | 7 | 7 |
| 上海-崇明区-招聘搜索 | 3 | 3 |
| 上海-徐汇区-招聘搜索 | 4 | 4 |
| 上海-普陀区-招聘搜索 | 5 | 5 |
| 上海-省考 | 27 | 27 |
| 上海-虹口区-事业单位 | 2 | 2 |
| 上海-长宁区-招聘搜索 | 3 | 3 |
| 上海-闵行区-招聘搜索 | 2 | 2 |
| 上海-静安区-招聘搜索 | 2 | 2 |
| 内蒙古-省考 | 4 | 4 |
| 北京-省考 | 9 | 9 |
| 天津-省考 | 7 | 7 |
| 广东-省考 | 1 | 1 |
| 新疆-省考 | 6 | 6 |
| 新疆兵团-省考 | 9 | 9 |
| 江苏-省属事业单位 | 40 | 40 |
| 江苏-省考 | 12 | 12 |
| 江苏南京-事业单位 | 34 | 34 |
| 江苏常州-事业单位 | 39 | 39 |
| 江苏无锡-事业单位 | 4 | 4 |
| 江苏泰州-事业单位 | 40 | 40 |
| 江苏淮安-事业单位 | 9 | 9 |
| 江苏盐城-事业单位 | 34 | 34 |
| 江苏苏州-事业单位 | 2 | 2 |
| 江苏苏州-人社招聘搜索 | 7 | 7 |
| 江苏连云港-事业单位 | 10 | 10 |
| 泉州-事业单位招聘 | 4 | 4 |
| 浙江-事业单位(通用网报平台) | 631 | 631 |
| 浙江宁波-事业单位(人才网招考) | 114 | 114 |
| 海南-省考 | 1 | 1 |
| 湖北-省考 | 2 | 2 |
| 湖南-省考 | 6 | 6 |
| 甘肃-省考 | 1 | 1 |
| 福州-事业单位招聘 | 10 | 10 |
| 福建-全省事业单位(统一平台) | 517 | 517 |
| 福建-省考 | 9 | 9 |
| 福建（省直）-事业单位招聘 | 11 | 11 |
| 黑龙江-省考 | 2 | 2 |

## B. 必须开机（云端抓不到，本机正常）—— 26 个

| 源 | 本机条数 | 云端报错 |
|---|---|---|
| 上海-金山区-事业单位 | 8 | <urlopen error timed out> |
| 云南-省考 | 1 | HTTP Error 403: Forbidden |
| 厦门-事业单位招聘 | 5 | <urlopen error timed out> |
| 四川成都-事业单位 | 15 | <urlopen error timed out> |
| 宁夏-省考 | 6 | <urlopen error timed out> |
| 安徽-省考 | 39 | HTTP Error 403: Forbidden |
| 山西-省考 | 28 | <urlopen error timed out> |
| 广西-省考 | 1 | <urlopen error timed out> |
| 江苏宿迁-事业单位 | 2 | HTTP Error 493:  |
| 江苏宿迁-人社招聘搜索 | 5 | raw=0 |
| 江苏扬州-事业单位 | 40 | HTTP Error 403: Forbidden |
| 江苏镇江-事业单位 | 8 | <urlopen error timed out> |
| 河北-省直事业单位 | 3 | JSON 接口未返回有效列表（检查签名头/接口路径） |
| 河北-省考 | 4 | JSON 接口未返回有效列表（检查签名头/接口路径） |
| 河南-省考 | 9 | HTTP Error 403: Forbidden |
| 浙江-事业单位招聘 | 40 | TLS 握手 6s 内未完成（rlsbt.zj.gov.cn:443），判定该域对当前出口不可达：_ssl.c:999: The hands |
| 浙江-省考 | 12 | timed out |
| 浙江丽水-事业单位 | 21 | timed out |
| 浙江嘉兴-事业单位 | 37 | TLS 握手 6s 内未完成（rlsbj.jiaxing.gov.cn:443），判定该域对当前出口不可达：_ssl.c:999: The  |
| 浙江温州-事业单位 | 11 | timed out |
| 浙江舟山-事业单位 | 31 | timed out |
| 贵州-省考 | 3 | <urlopen error timed out> |
| 辽宁-省考 | 5 | <urlopen error timed out> |
| 重庆-省考 | 1 | <urlopen error timed out> |
| 陕西-省考 | 5 | <urlopen error timed out> |
| 青海-省考 | 4 | <urlopen error timed out> |

## C. 两边都抓不到（源本身的问题，不是 IP）—— 3 个

| 源 | 云端 | 本机 |
|---|---|---|
| 上海-松江区-事业单位 | raw=250 | raw=250 |
| 江苏镇江-人社招聘搜索 | raw=0 | raw=23 |
| 龙岩-事业单位招聘 | Remote end closed connection without response | raw=74 |

## D. 浏览器源（比的是网络可达性，不是渲染能力）

- 云端 http 不通、本机 http 通 → **必须开机**：13 个
    - 上海-嘉定区-事业单位（本机 http_usable；云端 <urlopen error timed out>）
    - 四川-省考（本机 http_usable；云端 <urlopen error timed out>）
    - 山东-省考（本机 http_reachable_but_0；云端 HTTP Error 403: Forbidden）
    - 江苏南通-事业单位（本机 http_usable；云端 HTTP Error 403: Forbidden）
    - 江苏徐州-事业单位（本机 http_reachable_but_0；云端 <urlopen error timed out>）
    - 江西-省考（本机 http_reachable_but_0；云端 <urlopen error timed out>）
    - 浙江台州-事业单位（本机 http_reachable_but_0；云端 <urlopen error _ssl.c:999: The handshake operation）
    - 浙江宁波-事业单位（本机 http_reachable_but_0；云端 timed out）
    - 浙江杭州-事业单位（本机 http_reachable_but_0；云端 <urlopen error _ssl.c:999: The handshake operation）
    - 浙江湖州-事业单位（本机 http_reachable_but_0；云端 <urlopen error _ssl.c:999: The handshake operation）
    - 浙江绍兴-事业单位（本机 http_reachable_but_0；云端 timed out）
    - 浙江衢州-事业单位（本机 http_usable；云端 timed out）
    - 浙江金华-事业单位（本机 http_reachable_but_0；云端 <urlopen error _ssl.c:999: The handshake operation）
- 两边 http 都通（云端装了 Chromium 才有机会）：6 个
    - 上海-杨浦区-事业单位（云端 http_reachable_but_0 / 本机 http_reachable_but_0）
    - 上海-浦东区-事业单位（云端 http_reachable_but_0 / 本机 http_reachable_but_0）
    - 上海-青浦区-事业单位（云端 http_reachable_but_0 / 本机 http_reachable_but_0）
    - 上海-黄浦区-事业单位（云端 http_reachable_but_0 / 本机 http_reachable_but_0）
    - 吉林-省考（云端 http_reachable_but_0 / 本机 http_reachable_but_0）
    - 国考-2027年度专题（云端 http_reachable_but_0 / 本机 http_reachable_but_0）
- 两边 http 都不通：1 个
    - 西藏-省考（<urlopen error timed out>）
