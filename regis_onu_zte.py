import csv, os, time, threading, re
import paramiko
from concurrent.futures import ThreadPoolExecutor, as_completed

# ===================== KONFIGURASI DASAR =====================
SEND_DELAY_SEC        = 0.1      # jeda antar baris command
READ_WINDOW_SEC       = 1       # waktu baca tiap baris
LOG_CSV               = "hasil_registrasi.csv"

BATCH_SIZE            = 32       # jumlah ONU per batch
BATCH_DELAY_SEC       = 5        # jeda antar batch
MAX_COMMIT_WAIT_SEC   = 180      # max tunggu commit
COMMIT_POLL_SEC       = 10       # interval polling commit

progress_lock = threading.Lock()
progress_dict = {}
# =============================================================


# ---------------------- UTIL LOG / CSV -----------------------
def _key_of(row):
    return (row.get("interface","").strip(),
            row.get("onu_id","").strip(),
            row.get("sn","").strip())

def load_status_map(log_path: str):
    status_map = {}
    if not os.path.exists(log_path):
        return status_map
    with open(log_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            status_map[_key_of(row)] = row.get("status","").lower()
    return status_map

def append_log(lock, path, row_dict):
    with lock:
        rows = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

        updated = False
        for r in rows:
            if _key_of(r) == (_key_of(row_dict)):
                r.update(row_dict)
                updated = True
                break

        if not updated:
            rows.append(row_dict)

        with open(path, "w", newline="", encoding="utf-8") as f:
            fieldnames = ["interface", "onu_id", "sn", "name", "status", "message"]
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
            f.flush()


# ---------------------- SSH / CLI HELPER ---------------------
def ssh_connect(cfg):
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(cfg["host"], port=cfg["port"], username=cfg["user"], password=cfg["pass"], look_for_keys=False)
    sh = cli.invoke_shell()
    time.sleep(0.5)
    if sh.recv_ready():
        _ = sh.recv(65535)
    return cli, sh


def send_block(shell, block, delay=0.3, read_window=3):
    if not block.endswith("\n"):
        block += "\n"
    shell.send(block)
    time.sleep(delay)
    out = []
    end = time.time() + read_window
    last_data = time.time()

    while time.time() < end:
        time.sleep(0.1)
        while shell.recv_ready():
            chunk = shell.recv(65535).decode(errors="ignore")
            out.append(chunk)
            last_data = time.time()
            if "#" in chunk:
                return "".join(out)
        if time.time() - last_data > 0.8:  # tidak ada data baru → lanjut
            break
    return "".join(out)


def enter_exec(shell):
    send_block(shell, "enable")
    send_block(shell, "terminal length 0")

def enter_config(shell):
    send_block(shell, "enable")
    send_block(shell, "configure terminal")
    time.sleep(0.2)


# ----------------- BLOCK BUILDER (REGISTER/CONFIG) -----------
def build_register_block(rows, onu_type="ALL"):
    if not rows:
        return ""
    interface = rows[0]["interface"].strip()
    cmds = [f"interface {interface}"]
    for r in rows:
        onu_id = r["onu_id"].strip()
        sn = r["sn"].strip()
        cmds.append(f"no onu {onu_id}")
        cmds.append(f"onu {onu_id} type {onu_type} sn {sn}")
    cmds.append("exit")
    return "\n".join(cmds)

def build_config_block(row, vlan_prefix):
    interface = row["interface"].strip()
    onu_id = row["onu_id"].strip()
    name = row.get("name","").strip().replace(" ", "_")
    desc = row.get("description","").strip()
    profile = row.get("profile","").strip()
    username = row.get("username","").strip()
    password = row.get("password","").strip()
    vlan_inet = row.get("vlan_inet","").strip()
    vlan_hot  = row.get("vlan_hotspot","").strip()
    ssid = row.get("wifi_ssid","").strip()

    onu_iface = f"gpon-onu_{interface.split('_')[1]}:{onu_id}"
    vlan_prof = f"{vlan_prefix}{vlan_inet}"

    return f"""
interface {onu_iface}
name {name}
description {desc}
tcont 1 profile {profile}
gemport 1 tcont 1
service-port 1 vport 1 user-vlan {vlan_inet} vlan {vlan_inet}
service-port 2 vport 1 user-vlan {vlan_hot} vlan {vlan_hot}
exit

pon-onu-mng {onu_iface}
service pppoe gemport 1 vlan {vlan_inet}
wan-ip 1 mode pppoe username {username} password {password} vlan-profile {vlan_prof} host 1
service hotspot gemport 1 vlan {vlan_hot}
vlan port wifi_0/4 mode tag vlan {vlan_hot}
ssid auth wep wifi_0/4 open-system
ssid ctrl wifi_0/4 name {ssid}
interface wifi wifi_0/4 state unlock
security-mgmt 1 state enable mode forward protocol web
exit
""".strip()+"\n"


# -------------------- VERIFIKASI COMMIT OLT ------------------
def parse_onu_ids_from_show(output: str) -> set:
    """
    Parse hasil 'show gpon onu state gpon-olt_x/x/x' dari OLT ZTE.
    Akan menangkap pola seperti:
        1/2/6:1
        1/2/6:128
    """
    ids = set()
    clean = output.replace("\r", "").replace("\t", " ")
    lines = clean.splitlines()

    # Regex cocok untuk pola '1/2/6:128' dst.
    pattern = re.compile(r"\b\d+/\d+/\d+:(\d+)\b")

    for line in lines:
        m = pattern.search(line)
        if m:
            ids.add(m.group(1))

    return ids


def send_block(shell, block, delay=SEND_DELAY_SEC, read_window=READ_WINDOW_SEC):
    """
    Kirim perintah ke OLT (dengan dukungan auto-scroll '--More--').
    Akan terus menekan 'spasi' sampai semua output tampil.
    """
    if not block.endswith("\n"):
        block += "\n"

    shell.send(block)
    time.sleep(delay)

    out = []
    end_time = time.time() + read_window

    while time.time() < end_time:
        time.sleep(0.2)
        while shell.recv_ready():
            chunk = shell.recv(65535).decode(errors="ignore")
            out.append(chunk)

            # ⬇️ Auto-scroll untuk ZTE yang punya '--More--'
            if "--More--" in chunk or "-- More --" in chunk:
                shell.send(" ")               # tekan spasi
                time.sleep(0.3)
                end_time = time.time() + read_window  # perpanjang waktu baca

            # perpanjang sedikit waktu saat prompt akhir muncul
            if "#" in chunk:
                end_time = time.time() + 1.5

    return "".join(out)


def wait_until_committed(shell, interface, expected_ids: set, timeout=MAX_COMMIT_WAIT_SEC, poll=COMMIT_POLL_SEC):
    """
    Menunggu hingga semua ONU di interface tertentu benar-benar muncul di daftar OLT.
    Tambahan:
    - terminal length 0 agar tidak terpotong oleh '--More--'
    - retry re-check setelah timeout
    """
    send_block(shell, "enable")
    send_block(shell, "terminal length 0")

    start = time.time()
    last_seen = set()

    while time.time() - start < timeout:
        out = send_block(shell, f"show gpon onu state {interface}", delay=1, read_window=20)
        seen = parse_onu_ids_from_show(out)
        if seen:
            last_seen = seen
        matched = expected_ids.intersection(seen)
        print(f"⏳ Commit progress {len(matched)}/{len(expected_ids)} (seen total: {len(seen)})...")
        if matched == expected_ids:
            print(f"✅ Commit selesai: {len(matched)}/{len(expected_ids)} ONU muncul di {interface}")
            return matched
        time.sleep(poll)

    # 🔁 Re-check tambahan setelah timeout
    missing = expected_ids - last_seen
    if missing:
        print(f"⚠️ Timeout, {len(missing)} ONU belum muncul. Tunggu 2 menit lalu re-check...")
        time.sleep(120)
        send_block(shell, "enable")
        send_block(shell, "terminal length 0")
        out = send_block(shell, f"show gpon onu state {interface}", delay=1, read_window=20)
        seen = parse_onu_ids_from_show(out)
        matched = expected_ids.intersection(seen)
        print(f"🔁 Re-check: {len(matched)}/{len(expected_ids)} sudah muncul setelah retry.")
        return matched

    return last_seen.intersection(expected_ids)




# ----------------------- PROSES INTI -------------------------
def process_register(interface, rows, log_lock, cfg):
    """
    Optimized OLT-safe version:
    - Register per batch (default 32)
    - Tiap ±96 ONU lakukan auto-flush ('write' + reconnect)
    - Cegah buffer overflow (kasus 110 ONU hilang)
    - Retry SSH jika drop
    """
    cli, sh = ssh_connect(cfg)
    try:
        enter_config(sh)
        with progress_lock:
            progress_dict[interface] = {"done": 0, "total": len(rows), "status": "RUNNING"}

        total_batches = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
        batch_counter = 0

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i+BATCH_SIZE]
            block = build_register_block(batch, onu_type=cfg.get("onu_type", "ALL"))
            batch_no = i // BATCH_SIZE + 1
            batch_counter += len(batch)

            print(f"🛰️ Mengirim batch {batch_no}/{total_batches}, {len(batch)} ONU...")

            try:
                out = send_block(sh, block)
                if not out.strip():
                    raise Exception("Output kosong setelah kirim batch")
            except Exception as e:
                print(f"⚠️ SSH drop/batch gagal di batch {batch_no}: {e}")
                try: cli.close()
                except Exception: pass
                cli, sh = ssh_connect(cfg)
                enter_config(sh)
                print(f"🔁 Reconnect & ulang batch {batch_no}...")
                out = send_block(sh, block)

            # Simpan log CLI
            with open("olt_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"\n--- REGISTER {interface} BATCH {batch_no} ---\n{out}\n")

            # Update status CSV
            for r in batch:
                append_log(log_lock, LOG_CSV, {
                    "interface": r["interface"],
                    "onu_id": r["onu_id"],
                    "sn": r["sn"],
                    "name": r.get("name", ""),
                    "status": "pending",
                    "message": "Command sent, waiting for OLT commit"
                })

            # 🔸 Delay antar batch
            print(f"✅ Batch {batch_no}/{total_batches} selesai dikirim, jeda {BATCH_DELAY_SEC}s...\n")
            time.sleep(BATCH_DELAY_SEC)

            # 🔸 Auto flush tiap 96 ONU (hindari 110-limit bug)
            if batch_counter >= 96:
                print("💾 Flush buffer OLT (write + reconnect agar commit sempurna)...")
               # Setelah commit check selesai
                if cfg.get("auto_write", False):
                    print("💾 Menyimpan konfigurasi ke OLT (write)...")
                    send_block(sh, "write", delay=1, read_window=10)
                    print("✅ Konfigurasi berhasil disimpan ke flash.")
                else:
                    print("⚠️ 'write' dilewati (manual mode). Jalankan 'write' di CLI OLT setelah verifikasi.")

                cli.close()
                time.sleep(2)
                cli, sh = ssh_connect(cfg)
                enter_config(sh)
                batch_counter = 0

        # --- Verifikasi commit ---
        expected_ids = {r["onu_id"].strip() for r in rows}
        print(f"🕒 Semua batch terkirim. Tunggu OLT commit port {interface}...\n")
        time.sleep(2)
        committed = wait_until_committed(sh, interface, expected_ids)

        # --- Update hasil akhir ---
        for r in rows:
            onu_id = r["onu_id"].strip()
            if onu_id in committed:
                append_log(log_lock, LOG_CSV, {
                    "interface": r["interface"],
                    "onu_id": onu_id,
                    "sn": r["sn"],
                    "name": r.get("name", ""),
                    "status": "registered",
                    "message": "OLT commit confirmed"
                })
                with progress_lock:
                    progress_dict[interface]["done"] += 1
            else:
                append_log(log_lock, LOG_CSV, {
                    "interface": r["interface"],
                    "onu_id": onu_id,
                    "sn": r["sn"],
                    "name": r.get("name", ""),
                    "status": "pending",
                    "message": "Still waiting for OLT commit (skipped in config)"
                })

    finally:
        cli.close()
        with progress_lock:
            progress_dict[interface]["status"] = "FINISHED"





def process_config(interface, rows, log_lock, cfg, parallel_workers=None):
    """
    Versi stabil:
    - ≤36 ONU → 1 worker
    - >36 ONU → 2 worker
    - Auto-reconnect kalau SSH drop
    - Delay antar koneksi agar OLT tidak menolak session
    """
    status_map = load_status_map(LOG_CSV)
    to_config = [r for r in rows if status_map.get(_key_of(r)) == "registered"]

    if not to_config:
        print("Tidak ada ONU berstatus 'registered' untuk dikonfigurasi.")
        with progress_lock:
            progress_dict[interface] = {"status": "FINISHED"}
        return

    total = len(to_config)

    # 🧠 Tentukan jumlah worker otomatis
    if parallel_workers is None:
        parallel_workers = 1 if total <= 36 else 2

    print(f"⚙️ Total {total} ONU → Jalankan dengan {parallel_workers} worker (stabil mode).")

    with progress_lock:
        progress_dict[interface] = {"done": 0, "total": total, "status": "RUNNING"}

    # 🔹 Bagi ONU merata per worker
    chunks = [to_config[i::parallel_workers] for i in range(parallel_workers)]

    def worker_thread(worker_id, subset):
        print(f"🧩 Worker-{worker_id} mulai, {len(subset)} ONU")
        cli, sh = None, None

        def safe_connect():
            nonlocal cli, sh
            try:
                if cli:
                    cli.close()
                cli, sh = ssh_connect(cfg)
                enter_config(sh)
                print(f"🔁 Worker-{worker_id}: SSH reconnected.")
            except Exception as e:
                print(f"❌ Worker-{worker_id}: Gagal reconnect SSH ({e}), retry 5s...")
                time.sleep(5)
                safe_connect()

        # koneksi awal
        safe_connect()

        for r in subset:
            try:
                block = build_config_block(r, cfg["vlan_prefix"])
                out = send_block(sh, block)

                if "%Error" in out or "Invalid" in out:
                    status, msg = "error", "Error during config"
                else:
                    status, msg = "success", "Configured OK"

            except Exception as e:
                # jika koneksi drop, reconnect dan ulang perintah
                print(f"⚠️ Worker-{worker_id}: Exception saat ONU {r['onu_id']} → {e}")
                if "WinError 10054" in str(e) or "closed" in str(e).lower():
                    safe_connect()
                    try:
                        block = build_config_block(r, cfg["vlan_prefix"])
                        out = send_block(sh, block)
                        status, msg = "success", "Configured after reconnect"
                    except Exception as e2:
                        status, msg = "error", f"Retry failed: {e2}"
                else:
                    status, msg = "error", f"Exception: {e}"

            append_log(log_lock, LOG_CSV, {
                "interface": r["interface"],
                "onu_id": r["onu_id"],
                "sn": r["sn"],
                "name": r.get("name", ""),
                "status": status,
                "message": msg
            })

            with progress_lock:
                progress_dict[interface]["done"] += 1

        print(f"✅ Worker-{worker_id} selesai ({len(subset)} ONU).")
        if cli:
            cli.close()

    # 🔹 Jalankan worker dengan jeda antar koneksi
    threads = []
    for i, subset in enumerate(chunks, start=1):
        t = threading.Thread(target=worker_thread, args=(i, subset))
        t.start()
        threads.append(t)
        time.sleep(2)  # jeda antar koneksi agar OLT tidak overload

    for t in threads:
        t.join()

    with progress_lock:
        progress_dict[interface]["status"] = "FINISHED"

    print(f"🎯 Semua {total} ONU di {interface} selesai dikonfigurasi (mode stabil).")





# --------------------------- MAIN ----------------------------
def main(csv_path, cfg, mode="full"):
    print(f"🟢 MAIN DIPANGGIL: mode={mode}, file={csv_path}")
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    if not reader:
        print("❌ CSV kosong.")
        return

    ports = {r["interface"].strip() for r in reader if r.get("interface")}
    if len(ports) != 1:
        raise ValueError(f"❌ File harus berisi 1 port saja, ditemukan {len(ports)} port: {', '.join(ports)}")

    interface = list(ports)[0]
    log_lock = threading.Lock()

    status_map = load_status_map(LOG_CSV)
    to_register = [r for r in reader if status_map.get(_key_of(r)) not in {"registered", "success"}]
    to_config = [r for r in reader if status_map.get(_key_of(r)) != "success"]

    if mode in ["register", "full"]:
        if to_register:
            print(f"🚀 REGISTER di {interface}: {len(to_register)} ONU (batch {BATCH_SIZE})")
            with progress_lock:
                progress_dict[interface] = {"done": 0, "total": len(to_register), "status": "WAITING"}
            process_register(interface, to_register, log_lock, cfg)
        else:
            print("✅ Semua ONU sudah registered/success. Lewati tahap register.")

    if mode in ["config", "full"]:
        print(f"⚙️ CONFIG di {interface}: eksekusi hanya untuk 'registered' (skip pending/success).")
        workers = None
        if "config_workers" in cfg and str(cfg["config_workers"]).isdigit():
            workers = int(cfg["config_workers"])

        process_config(interface, to_config, log_lock, cfg, parallel_workers=workers)


    print("🎉 Semua proses selesai.")
    return "done"
