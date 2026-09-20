# 环境级坑与处置（批量仓库评测实测汇总，2026-09-07）

## 1. 老 sdist 包在新 pip 下解包 EEXIST

现象：`pip install <pkg>` 报 `OSError: EEXIST: file already exists, mkdir .../pip-install-xxx/<pkg>_...`，
且 `pip download` 同样挂（pip 下载时也会解包读元数据）。老包（如 jsonpath 0.82、easyutils 0.1.7）
的 sdist tar 内含重复目录条目，撞上新 pip/新 Python 的严格解包。

处置（按序尝试）：
1. 该包是否有 wheel 或新版替代（优先）。
2. 从 PyPI JSON API 直拉 sdist 用系统 tar 解（bsdtar 容忍重复条目），再从目录安装：
   ```bash
   url=$(curl -s https://pypi.org/pypi/<pkg>/<ver>/json | jq -r '.urls[] | select(.packagetype=="sdist") | .url')
   curl -sL -o x.tar.gz "$url" && tar xf x.tar.gz && cd <pkg>-<ver> && python -m pip install .
   ```
3. 需要长期依赖（进 requirements）：从解包目录 `pip wheel --no-deps -w vendor/wheels .` 造 wheel，
   requirements.lock 里用 `./vendor/wheels/<pkg>-<ver>-py3-none-any.whl`（wheel 安装不走 sdist 解包路径，
   CI 可复现）。wheel URL 可带 sha256。

## 2. pip / python requests 走死系统代理

macOS 上 `requests`/`pip` 除了环境变量还会读**系统级代理配置**（SystemConfiguration）。
会话 shell 里的 HTTP_PROXY 可能指向已死端口。症状：ProxyError 'Cannot connect to proxy'。

处置：
- pip：命令前缀 `NO_PROXY='*' no_proxy='*'`（临时目录建议同时换 `TMPDIR=/tmp/...`）。
- python 脚本：顶部 `os.environ` 清 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 并设 `NO_PROXY='*'`。
  注意 curl 的 `--noproxy '*'` 思路对 python 不等价——清环境变量不够，NO_PROXY 必须显式设。
- 若某域名必须走活代理：显式 `proxies={"http": ..., "https": ...}` 传给请求方，并确认中间库
  （如 akshare 的封装函数）真的会把参数透传（lambda 包装参数被吞是常见测试 bug）。

## 3. 同仓库不同数据域成败迥异

一个库的各接口背后是不同上游域名，可用性要**逐接口实测**，不能凭"这个库能用"推广：
例（akshare，2026-09-07 实测）：push2ex/datacenter-web/sina 域 4/4 稳定 150-250ms；
push2/push2his 域直连被远端断连（SNI 阻断）、走活代理也被拒，0/6。
处置：集成时只接可用域的接口；在服务层注释可用域矩阵与测量日期；被墙面明确不接。

## 4. 大依赖安装超时

cvxpy/scipy/pandas 全家桶在负载高的机器上可能超过前台命令超时（默认 120s）。
处置：`run_in_background` + 日志落盘 + 轮询就绪标记（如 `python -c "import pkg; print('READY')"`）。
失败重试前先 `pip cache purge` 或换 `TMPDIR`。

## 5. multiprocessing 脚本自递归卡死

库内部用 multiprocessing（如 backtest_many）时，macOS spawn 模式下子进程重导入 `__main__`，
脚本顶层代码反复执行（表现为同一行 print 出现多次、进程数翻倍、永不结束）。
处置：整个脚本主体包进 `if __name__ == "__main__":`；先 `pkill -f <script>` 清残存进程。

## 6. 诊断长驻进程/卡死脚本的通用套路

1. `sample <pid> 2 -file /tmp/x.txt` 看栈热点——全部样本落在一个函数即为元凶（如沙箱 shim 的
   同步 broker IPC 自旋）。
2. 对照实验分离变量：同机起一个最小纯净进程跑同样操作，快 = 进程特有问题，慢 = 机器级问题。
3. 从沙箱/工具会话起长驻 node 服务：先 `unset NODE_OPTIONS DYLD_*`（注入 shim 可能把事件循环
   吃光），并用启动器脚本固化 cwd。
4. 测试深夜跑红白天全绿 → 查时间窗敏感：UTC naive 时间被当本地解释（`.astimezone()` 对 naive
   datetime 的行为）、日期翻转。正确写法：`naive_utc.replace(tzinfo=timezone.utc).astimezone()`。

## 7. 评测数据源准备

回测/优化类仓库验证时优先用**真实行情数据**（可信度远高于随机数）：同批评测的数据仓库
（如 akshare 稳定域）现拉的 CSV 即可；注意某些源 volume 字段单位（股 vs 额）与复权口径。
