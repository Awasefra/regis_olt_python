## Deskripsi

Aplikasi ini adalah sistem otomatis registrasi ONU ZTE (C300/C600) yang menggunakan Python + Flask (UI Web) dan Paramiko (SSH Automation).
Kamu dapat meng-upload file .csv berisi daftar ONU dan sistem akan melakukan konfigurasi secara paralel melalui SSH ke OLT.

🏗️ Struktur Proyek
```
regis_olt_python/
├─ olt_web_ui.py                 ← Web UI Flask (upload + progress bar)
├─ regis_onu_zte.py      ← Worker SSH registrasi ONU
├─ uploads/                      ← Folder tempat upload file CSV
├─ hasil_registrasi.csv          ← Hasil log registrasi ONU
├─ .env                          ← File konfigurasi OLT (aman)
├─ requirements.txt              ← Daftar dependensi Python
└─ README.md
```

## Clone Project
```
git clone https://github.com/Awasefra/regis_olt_python.git
cd regis_olt_python
```

### Copy .env.example jadi .env
```
APP_PORT=8000
FLASK_DEBUG=true #production = false

OLT_HOST=192.168.1.22
OLT_PORT=22
OLT_USER=root
OLT_PASS=zte123
MAX_WORKERS=6
VLAN_PROFILE_PREFIX=customer
```


## Instalasi & Jalankan (Lokal)


### Buat Virtual Environment
```
python -m venv venv
venv\Scripts\activate      # (Windows)
# atau
source venv/bin/activate   # (Linux/Mac)
```

### Install Dependencies
```
pip install -r requirements.txt
```

### Jalankan Aplikasi
```
python olt_web_ui.py
```
Akses di browser:
http://localhost:8000


## Jalankan di Server Linux Ubuntu or Debian (Production)

### Buat & aktifkan virtualenv
```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Jalankan manual (tes)
```
python olt_web_ui.py
```

Atau:
```
gunicorn -w 4 -b 127.0.0.1:8000 olt_web_ui:app
```
Lalu buka di browser sesuai ip dan port
``
http://<ip-server>:8000
``

### (Opsional) Integrasi Nginx
```
/etc/nginx/sites-available/olt-regis.conf

server {
    listen 80;
    server_name name.local;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Aktifkan:
```
sudo ln -s /etc/nginx/sites-available/olt-regis.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

###Format CSV Input

Gunakan header berikut:
```
interface,onu_id,sn,name,description,profile,username,password,vlan_inet,vlan_hotspot,wifi_ssid
gpon-olt_1/2/6,1,ZTEEEE,Siti ,G318273773,10M,G300424772,password,130,131,company-number
```
### Fitur Utama
```
✅ Upload CSV langsung via Web UI
✅ Progress bar real-time per-port & total
✅ SSH multi-threaded (6–8 paralel port)
✅ Auto-log hasil ke hasil_registrasi.csv
✅ Command “write” otomatis di akhir (save config ke flash)
✅ Resume otomatis jika CSV diulang (skip yang sudah sukses)
✅ Bisa dijalankan via Flask dev mode atau Gunicorn
``` 

### Lisensi

MIT License — bebas digunakan untuk internal network automation.