from flask import Flask, request, render_template_string, jsonify
import threading, os, csv
from regis_onu_zte import main as run_registration, progress_dict

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
LOG_CSV = "hasil_registrasi.csv"


@app.route("/")
def index():
    return render_template_string("""
    <html>
    <head>
      <title>OLT ZTE Auto Registration</title>
      <style>
        :root {
          --bg: #121212;
          --card: #1e1e1e;
          --border: #2a2a2a;
          --text: #e0e0e0;
          --accent: #00bcd4;
          --success: #4CAF50;
          --wait: #FFC107;
          --done: #673AB7;
        }
        body {
          background: var(--bg);
          color: var(--text);
          font-family: "Segoe UI", Arial, sans-serif;
          padding: 30px;
          max-width: 1000px;
          margin: auto;
        }
        h2, h3 { color: #fff; }
        form {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 15px 25px;
          background: var(--card);
          padding: 20px;
          border-radius: 12px;
          border: 1px solid var(--border);
        }
        form label {
          font-size: 13px;
          color: #aaa;
          display: block;
          margin-bottom: 4px;
        }
        input, button {
          padding: 9px;
          font-size: 14px;
          border-radius: 6px;
          border: 1px solid var(--border);
          background: #181818;
          color: var(--text);
          width: 100%;
          box-sizing: border-box;
        }
        button {
          cursor: pointer;
          background: linear-gradient(90deg, var(--accent), #2196f3);
          color: #fff;
          border: none;
          font-weight: bold;
          transition: 0.2s;
        }
        button:hover { opacity: 0.9; }
        .full-width { grid-column: 1 / 3; }
        .bar {
          height: 18px;
          background: var(--border);
          border-radius: 6px;
          overflow: hidden;
          margin-bottom: 8px;
        }
        .fill { height: 100%; transition: width 0.5s; }
        .legend { margin-top:10px; font-size:14px; }
        .legend span {
          display:inline-block; width:15px; height:15px;
          margin-right:5px; border-radius:3px;
        }
        table {
          border-collapse: collapse;
          width: 100%;
          margin-top:15px;
          background: var(--card);
          border-radius: 8px;
          overflow: hidden;
        }
        th, td {
          border: 1px solid var(--border);
          padding: 8px;
          text-align: left;
          font-size: 13px;
        }
        th { background: #2b2b2b; color: #fff; }
        tr:nth-child(even) { background: #181818; }
        #status { margin-top: 15px; font-weight: bold; }
        hr { border: 0; border-top: 1px solid var(--border); margin: 25px 0; }
        footer {
          text-align: center;
          color: #777;
          font-size: 13px;
          margin-top: 40px;
        }
        footer a {
          color: var(--accent);
          text-decoration: none;
        }
        footer a:hover { text-decoration: underline; }
      </style>
    </head>
    <body>
      <h2>📡 OLT ZTE Auto Registration</h2>

      <form id="uploadForm">
        <div>
          <label>OLT Host</label>
          <input type="text" name="olt_host" placeholder="10.240.x.x" required>
        </div>
        <div>
          <label>OLT Port</label>
          <input type="number" name="olt_port" value="22" required>
        </div>
        <div>
          <label>OLT User</label>
          <input type="text" name="olt_user" value="zte" required>
        </div>
        <div>
          <label>OLT Password</label>
          <input type="password" name="olt_pass" required>
        </div>
        <div>
          <label>VLAN Prefix</label>
          <input type="text" name="vlan_prefix" placeholder="vlan" required>
        </div>
        <div>
          <label>Max Workers (1-8)</label>
          <input type="number" name="max_workers" min="1" max="8" value="6" required>
        </div>
        <div class="full-width">
          <label>File CSV ONU</label>
          <input type="file" name="file" accept=".csv" required>
        </div>
        <div class="full-width">
          <button type="submit">🚀 Upload & Jalankan</button>
        </div>
      </form>

      <div id="status"></div>

      <hr>
      <button onclick="loadPending()">📄 Lihat Pending ONU</button>
      <div id="pending-area"></div>

      <hr>
      <div class="legend">
        <b>Keterangan:</b>
        <span style="background:var(--success);"></span>Running
        <span style="background:var(--wait);"></span>Waiting
        <span style="background:var(--done);"></span>Finished
      </div>
      <div id="progress-area"></div>

      <hr>
      <h3>📘 Hasil Registrasi</h3>
      <div id="results-area"></div>

      <footer>
        <p>© 2025 <a href="https://dasaria.id" target="_blank">Dasaria Development Team</a> — All rights reserved.</p>
        <p>Made with 💻 by Awanda Setya</p>
      </footer>

      <script>
      document.getElementById("uploadForm").addEventListener("submit", async function(e) {
        e.preventDefault();
        const form = e.target;
        const formData = new FormData(form);
        document.getElementById("status").innerHTML = "⏳ Mengupload dan memulai proses...";
        try {
          const res = await fetch("/upload", { method: "POST", body: formData });
          const text = await res.text();
          document.getElementById("status").innerHTML = text;
        } catch (err) {
          document.getElementById("status").innerHTML = "❌ Gagal upload: " + err;
        }
      });

      async function loadPending() {
        const res = await fetch("/pending");
        const text = await res.text();
        document.getElementById("pending-area").innerHTML = text;
      }

      async function refreshProgress() {
        const res = await fetch('/progress');
        const data = await res.json();
        let html = "";
        let totalDone = 0;
        let totalTotal = 0;
        for (const [port, v] of Object.entries(data)) {
          const pct = (v.total ? (v.done / v.total * 100) : 0).toFixed(0);
          let color = 'var(--success)';
          if (v.status === 'WAITING') color = 'var(--wait)';
          else if (v.status === 'FINISHED') color = 'var(--done)';
          totalDone += v.done;
          totalTotal += v.total;
          html += `<div><b>${port}</b> - ${v.status} (${v.done}/${v.total})</div>
                   <div class='bar'><div class='fill' style='width:${pct}%; background:${color}'></div></div>`;
        }
        if (totalTotal > 0) {
          const pctAll = (totalDone / totalTotal * 100).toFixed(0);
          html = `<h3>Total Progress: ${totalDone}/${totalTotal} (${pctAll}%)</h3>
                  <div class='bar'><div class='fill' style='width:${pctAll}%; background:var(--accent);'></div></div><br>` + html;
        }
        document.getElementById("progress-area").innerHTML = html;
        setTimeout(refreshProgress, 2000);
      }
      refreshProgress();

      async function loadResults() {
        const res = await fetch('/results');
        const html = await res.text();
        document.getElementById("results-area").innerHTML = html;
        setTimeout(loadResults, 5000);
      }
      loadResults();
      </script>
    </body>
    </html>
    """)


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file:
        return "❌ Tidak ada file diupload", 400

    path = os.path.join(UPLOAD_FOLDER, "data_onu.csv")
    file.save(path)

    olt_config = {
        "host": request.form.get("olt_host"),
        "port": int(request.form.get("olt_port")),
        "user": request.form.get("olt_user"),
        "pass": request.form.get("olt_pass"),
        "vlan_prefix": request.form.get("vlan_prefix"),
        "max_workers": min(8, max(1, int(request.form.get("max_workers", 6))))
    }

    t = threading.Thread(target=run_registration, args=(path, olt_config), daemon=True)
    t.start()
    return f"✅ File diterima. Proses registrasi dimulai ke OLT {olt_config['host']}:{olt_config['port']} (Thread: {olt_config['max_workers']})."


@app.route("/pending")
def pending():
    path = os.path.join(UPLOAD_FOLDER, "data_onu.csv")
    if not os.path.exists(path):
        return "❌ Belum ada file diupload.", 400

    from regis_onu_zte import load_done_keys
    done = load_done_keys(LOG_CSV)

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    pending = [r for r in rows if (r["interface"].strip(), r["onu_id"].strip(), r["sn"].strip()) not in done]
    if not pending:
        return "<b>✅ Semua baris sudah success di log. Tidak ada pending ONU.</b>"

    html = "<table><tr><th>Interface</th><th>ONU ID</th><th>SN</th><th>Nama</th></tr>"
    for p in pending:
        html += f"<tr><td>{p['interface']}</td><td>{p['onu_id']}</td><td>{p['sn']}</td><td>{p['name']}</td></tr>"
    html += "</table>"
    return html


@app.route("/progress")
def progress():
    return jsonify(progress_dict)


@app.route("/results")
def results():
    if not os.path.exists(LOG_CSV):
        return "<i>Belum ada hasil registrasi.</i>"
    with open(LOG_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return "<i>Belum ada hasil registrasi.</i>"

    html = "<table><tr>" + "".join([f"<th>{h}</th>" for h in rows[0].keys()]) + "</tr>"
    for r in rows[-50:]:
        html += "<tr>" + "".join([f"<td>{v}</td>" for v in r.values()]) + "</tr>"
    html += "</table>"
    return html


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True, use_reloader=False)
