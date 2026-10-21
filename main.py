
import network
import socket
import time
import machine
import os
import ujson

try:
    import urequests
except ImportError:
    import requests as urequests


# ==================================================
# 1. Wi-Fi 設定：修改這裡
# ==================================================
WIFI_SSID = "labor"
WIFI_PASSWORD = "98668800"

HOST = "esp32"


# ==================================================
# 2. OTA 版本設定：每次發布時修改版本號
# ==================================================
CURRENT_VERSION = "1.0.9"

VERSION_URL = (
    "https://pkimo.github.io/OTA1019TT/version.json"
)

ALLOWED_PREFIX = (
    "https://pkimo.github.io/OTA1019TT/"
)

OTA_CHECK_INTERVAL = 6 * 60 * 60  # 6 小時，單位秒

last_ota_check = time.time()


# ==================================================
# 3. 網頁登入設定
# ==================================================
USERNAME = "admin"
PASSWORD = "admin"


LOGIN_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32 Login</title>
</head>
<body>
<h2>ESP32 Login Page</h2>
<form action="/login" method="POST">
Username: <input name="userid"><br><br>
Password: <input type="password" name="pwd"><br><br>
<button type="submit">Login</button>
</form>
</body>
</html>
"""


OTA_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32 GitHub OTA</title>
</head>
<body>
<h2>ESP32 GitHub OTA</h2>
<p>Current version: VERSION_PLACEHOLDER</p>
<p>Automatic version checking is enabled.</p>
<form action="/check" method="POST">
<button type="submit">Check GitHub Version Now</button>
</form>
<p><a href="/">Back to login</a></p>
</body>
</html>
"""


# ==================================================
# 4. Wi-Fi 連線
# ==================================================
def setup_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    # 強制重新連線，避免沿用舊熱點
    if wlan.isconnected():
        wlan.disconnect()
        time.sleep(1)

    print("Connecting to:", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    timeout = 30

    while not wlan.isconnected() and timeout > 0:
        print(".", end="")
        time.sleep(1)
        timeout -= 1

    if not wlan.isconnected():
        raise RuntimeError("WiFi connection failed")

    print("\nWiFi connected")
    print("SSID:", wlan.config("essid"))
    print("IP:", wlan.ifconfig()[0])

    return wlan


# ==================================================
# 5. 版本比較
# ==================================================
def version_tuple(version):
    try:
        parts = tuple(int(x) for x in version.split("."))
        if len(parts) != 3:
            return None
        return parts
    except Exception:
        return None


def is_newer_version(remote, current):
    r = version_tuple(remote)
    c = version_tuple(current)

    if r is None or c is None:
        return False

    return r > c


# ==================================================
# 6. GitHub OTA：更新 main.py 程式檔
# ==================================================
def check_for_update():
    global last_ota_check

    wlan = network.WLAN(network.STA_IF)

    if not wlan.isconnected():
        print("[OTA] WiFi disconnected")
        return

    response = None

    try:
        print("[OTA] Checking GitHub version...")

        response = urequests.get(VERSION_URL)
        if response.status_code != 200:
            print("[OTA] HTTP error:", response.status_code)
            return

        data = response.json()

    except Exception as e:
        print("[OTA] Version request failed:", e)
        return

    finally:
        if response is not None:
            response.close()

    remote_version = data.get("version")
    firmware_url = data.get("firmware")

    if not isinstance(remote_version, str):
        print("[OTA] Missing version")
        return

    if not isinstance(firmware_url, str):
        print("[OTA] Missing firmware URL")
        return

    print("[OTA] Current:", CURRENT_VERSION)
    print("[OTA] GitHub:", remote_version)

    # 只允許指定 GitHub Pages 路徑
    if not firmware_url.startswith(ALLOWED_PREFIX):
        print("[OTA] Firmware URL rejected")
        return

    if not is_newer_version(remote_version, CURRENT_VERSION):
        print("[OTA] Already up to date")
        return

    print("[OTA] New version found")
    print("[OTA] Downloading Python program...")

    response = None

    try:
        response = urequests.get(firmware_url)

        if response.status_code != 200:
            print("[OTA] Download failed:", response.status_code)
            return

        # 這裡更新的是 MicroPython 程式，不是 Arduino .bin
        new_code = response.text

        if not new_code or len(new_code) < 10:
            print("[OTA] Invalid program")
            return

        # 基本語法檢查，避免明顯語法錯誤的程式覆蓋
        try:
            compile(new_code, "main.py", "exec")
        except Exception as e:
            print("[OTA] Syntax error:", e)
            return

        with open("main.py.new", "w") as f:
            f.write(new_code)

        print("[OTA] Download complete")

    except Exception as e:
        print("[OTA] Download error:", e)
        return

    finally:
        if response is not None:
            response.close()

    # 先備份舊程式，再替換新版
    try:
        try:
            os.remove("main.py.bak")
        except OSError:
            pass

        os.rename("main.py", "main.py.bak")
        os.rename("main.py.new", "main.py")

        print("[OTA] Update successful; rebooting")
        time.sleep(1)
        machine.reset()

    except Exception as e:
        print("[OTA] File replacement failed:", e)

        # 如果替換失敗，嘗試還原舊版
        try:
            os.rename("main.py.bak", "main.py")
        except Exception:
            pass


# ==================================================
# 7. HTTP 網頁伺服器
# ==================================================
def send_response(conn, status, content_type, body,
                  extra_headers=""):
    if isinstance(body, str):
        body = body.encode("utf-8")

    header = (
        "HTTP/1.1 " + status + "\r\n"
        "Content-Type: " + content_type + "\r\n"
        "Content-Length: " + str(len(body)) + "\r\n"
        "Connection: close\r\n"
        + extra_headers
        + "\r\n"
    )

    conn.send(header.encode("utf-8"))
    conn.sendall(body)


def url_decode(value):
    value = value.replace("+", " ")

    result = bytearray()
    i = 0

    while i < len(value):
        if value[i] == "%" and i + 2 < len(value):
            try:
                result.append(int(value[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass

        result.extend(value[i].encode())
        i += 1

    return result.decode("utf-8")


def parse_form(body):
    result = {}

    for item in body.split("&"):
        if "=" in item:
            key, value = item.split("=", 1)
            result[url_decode(key)] = url_decode(value)

    return result


def handle_request(conn):
    conn.settimeout(3)

    request = b""

    # 讀取 HTTP headers
    while b"\r\n\r\n" not in request:
        chunk = conn.recv(1024)

        if not chunk:
            return

        request += chunk

        if len(request) > 8192:
            return

    header_data, body = request.split(b"\r\n\r\n", 1)
    lines = header_data.decode("utf-8", "ignore").split("\r\n")

    if not lines:
        return

    request_line = lines[0].split()

    if len(request_line) < 2:
        return

    method = request_line[0]
    path = request_line[1]

    content_length = 0

    for line in lines[1:]:
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())

    # 讀取完整 POST body
    while len(body) < content_length:
        chunk = conn.recv(1024)

        if not chunk:
            return

        body += chunk

    body = body[:content_length].decode("utf-8", "ignore")

    print(method, path)

    if method == "GET" and path == "/":
        send_response(
            conn, "200 OK", "text/html; charset=utf-8",
            LOGIN_PAGE
        )

    elif method == "POST" and path == "/login":
        form = parse_form(body)

        if (
            form.get("userid") == USERNAME
            and form.get("pwd") == PASSWORD
        ):
            send_response(
                conn, "303 See Other", "text/plain",
                "Login successful",
                "Location: /serverIndex\r\n"
            )
        else:
            send_response(
                conn, "401 Unauthorized", "text/plain",
                "Wrong username or password"
            )

    elif method == "GET" and path == "/serverIndex":
        page = OTA_PAGE.replace(
            "VERSION_PLACEHOLDER", CURRENT_VERSION
        )

        send_response(
            conn, "200 OK", "text/html; charset=utf-8",
            page
        )

    elif method == "POST" and path == "/check":
        send_response(
            conn, "200 OK", "text/plain; charset=utf-8",
            "Version check started. See serial output."
        )

        check_for_update()

    else:
        send_response(
            conn, "404 Not Found", "text/plain", "Not found"
        )


# ==================================================
# 8. 啟動程式
# ==================================================
setup_wifi()

try:
    import socket

    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(addr)
    server.listen(2)
    server.settimeout(1)

    print("Web server started")
    print("Open http://" + network.WLAN(network.STA_IF).ifconfig()[0])

except Exception as e:
    raise RuntimeError("Web server startup failed: " + str(e))


# 開機時檢查一次
check_for_update()
last_ota_check = time.time()


while True:
    try:
        try:
            conn, remote = server.accept()

            try:
                print("Client:", remote)
                handle_request(conn)
            finally:
                conn.close()

        except OSError:
            # accept timeout，繼續執行背景工作
            pass

        # 每 6 小時自動檢查一次
        if time.time() - last_ota_check >= OTA_CHECK_INTERVAL:
            last_ota_check = time.time()
            check_for_update()

        time.sleep_ms(10)

    except Exception as e:
        print("Server error:", e)
        time.sleep(1)
