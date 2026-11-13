"""
Docker 컨테이너 지표 수집기 (Prometheus 형식, 표준 라이브러리만 사용)

cAdvisor 는 Docker Desktop 의 containerd 이미지 저장소(overlayfs)에서 컨테이너를 식별하지 못해서,
Docker API(/var/run/docker.sock)로 직접 상태·사용량을 읽는다. 덤으로 healthcheck 상태와 재시작 횟수도 나온다.

GET :9417/metrics
  docker_container_up{name,service}                 실행 중이면 1
  docker_container_health{name,service,status}      healthy / unhealthy / starting / none
  docker_container_restart_count                    Docker 가 재시작한 횟수
  docker_container_started_at_seconds               마지막 시작 시각 (가동 시간 = time() - 이 값)
  docker_container_cpu_usage_seconds_total          누적 CPU 사용 시간 → rate() 로 코어 사용량
  docker_container_memory_usage_bytes               사용 메모리 (페이지 캐시 제외, docker stats 와 같은 기준)
  docker_container_memory_limit_bytes
  docker_container_network_receive_bytes_total / _transmit_bytes_total
  docker_container_blkio_read_bytes_total / _write_bytes_total
  docker_container_pids
  docker_host_cpus / docker_host_memory_bytes
"""
import calendar
import http.client
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
PROJECT = os.getenv("COMPOSE_PROJECT", "rag-base")
PORT = int(os.getenv("PORT", "9417"))


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 10):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def docker_get(path: str):
    conn = UnixHTTPConnection(SOCKET)
    try:
        conn.request("GET", path)
        res = conn.getresponse()
        body = res.read()
        if res.status >= 400:
            raise RuntimeError(f"{path} → {res.status} {body[:200]!r}")
        return json.loads(body)
    finally:
        conn.close()


def esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def iso_to_epoch(s: str) -> float:
    """Docker 의 UTC 시각 '2026-11-13T07:48:12.123456789Z' → epoch 초"""
    if not s or s.startswith("0001"):
        return 0.0
    main, _, frac = s.rstrip("Z").partition(".")
    return calendar.timegm(time.strptime(main, "%Y-%m-%dT%H:%M:%S")) + float(f"0.{frac[:6] or '0'}")


def collect_one(c: dict):
    cid = c["Id"]
    labels = c.get("Labels") or {}
    name = (c.get("Names") or ["/?"])[0].lstrip("/")
    base = {"name": name, "service": labels.get("com.docker.compose.service", name)}
    inspect = docker_get(f"/containers/{cid}/json")
    state = inspect.get("State") or {}
    stats = docker_get(f"/containers/{cid}/stats?stream=false&one-shot=true") if state.get("Running") else {}
    return base, inspect, state, stats


def render() -> str:
    flt = quote(json.dumps({"label": [f"com.docker.compose.project={PROJECT}"]}))
    containers = docker_get(f"/containers/json?all=true&filters={flt}")
    info = docker_get("/info")
    out = []

    def metric(name, help_, type_, samples):
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {type_}")
        for labels, value in samples:
            lab = ",".join(f'{k}="{esc(v)}"' for k, v in labels.items())
            out.append(f"{name}{{{lab}}} {value}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(collect_one, containers))

    s = {k: [] for k in ("up", "health", "restart", "started", "cpu", "mem", "memlim", "rx", "tx", "rd", "wr", "pids",
                         "info")}
    for base, inspect, state, stats in rows:
        s["info"].append(({**base, "image": inspect.get("Config", {}).get("Image", ""),
                           "state": state.get("Status", "")}, 1))
        s["up"].append((base, 1 if state.get("Running") else 0))
        health = (state.get("Health") or {}).get("Status") or "none"
        for st in ("healthy", "unhealthy", "starting", "none"):
            s["health"].append(({**base, "status": st}, 1 if health == st else 0))
        s["restart"].append((base, inspect.get("RestartCount", 0)))
        s["started"].append((base, iso_to_epoch(state.get("StartedAt", ""))))
        if not stats:
            continue
        cpu = ((stats.get("cpu_stats") or {}).get("cpu_usage") or {}).get("total_usage", 0)
        s["cpu"].append((base, cpu / 1e9))
        mem = stats.get("memory_stats") or {}
        cache = (mem.get("stats") or {}).get("inactive_file", 0)
        s["mem"].append((base, max(0, mem.get("usage", 0) - cache)))
        s["memlim"].append((base, mem.get("limit", 0)))
        nets = stats.get("networks") or {}
        s["rx"].append((base, sum(n.get("rx_bytes", 0) for n in nets.values())))
        s["tx"].append((base, sum(n.get("tx_bytes", 0) for n in nets.values())))
        blk = (stats.get("blkio_stats") or {}).get("io_service_bytes_recursive") or []
        s["rd"].append((base, sum(b.get("value", 0) for b in blk if b.get("op", "").lower() == "read")))
        s["wr"].append((base, sum(b.get("value", 0) for b in blk if b.get("op", "").lower() == "write")))
        s["pids"].append((base, (stats.get("pids_stats") or {}).get("current", 0)))

    metric("docker_container_info", "컨테이너 정보", "gauge", s["info"])
    metric("docker_container_up", "실행 중이면 1", "gauge", s["up"])
    metric("docker_container_health", "healthcheck 상태", "gauge", s["health"])
    metric("docker_container_restart_count", "재시작 횟수", "gauge", s["restart"])
    metric("docker_container_started_at_seconds", "마지막 시작 시각(epoch)", "gauge", s["started"])
    metric("docker_container_cpu_usage_seconds_total", "누적 CPU 사용 시간", "counter", s["cpu"])
    metric("docker_container_memory_usage_bytes", "사용 메모리(캐시 제외)", "gauge", s["mem"])
    metric("docker_container_memory_limit_bytes", "메모리 한도", "gauge", s["memlim"])
    metric("docker_container_network_receive_bytes_total", "수신 바이트", "counter", s["rx"])
    metric("docker_container_network_transmit_bytes_total", "송신 바이트", "counter", s["tx"])
    metric("docker_container_blkio_read_bytes_total", "디스크 읽기", "counter", s["rd"])
    metric("docker_container_blkio_write_bytes_total", "디스크 쓰기", "counter", s["wr"])
    metric("docker_container_pids", "프로세스 수", "gauge", s["pids"])
    metric("docker_host_cpus", "Docker 호스트 CPU 수", "gauge", [({}, info.get("NCPU", 0))])
    metric("docker_host_memory_bytes", "Docker 호스트 메모리", "gauge", [({}, info.get("MemTotal", 0))])
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] not in ("/metrics", "/"):
            self.send_error(404)
            return
        try:
            body = render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            body = f"# 수집 실패: {e}\n".encode()
            self.send_response(500)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 스크랩마다 접속 로그 남기지 않음
        pass


if __name__ == "__main__":
    print(f"docker exporter :{PORT} project={PROJECT} socket={SOCKET}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
