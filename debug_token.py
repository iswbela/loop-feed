import requests
from bs4 import BeautifulSoup

BASE_URL   = "https://kommons.com"
LOGIN_URL  = f"{BASE_URL}/members-area/login"
USER       = "seu_email@exemplo.com"
PASSWORD   = "sua_senha"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

def sep(titulo):
    return None

def mostrar_cookies(session):
    return None

def extrair_token(html):
    soup = BeautifulSoup(html, "html.parser")
    inp = soup.find("input", {"name": "grt"})
    if inp:
        val = inp.get("value", "")
        return val if val else None
    return None

sep("ABORDAGEM 1: GET home → GET login")
s1 = requests.Session()
s1.get(BASE_URL, headers=HEADERS, timeout=15)
mostrar_cookies(s1)

r = s1.get(LOGIN_URL, headers={**HEADERS, "Referer": BASE_URL}, timeout=15)
token = extrair_token(r.text)

if token:
    sep("TOKEN ENCONTRADO! Tentando login...")
    payload = {"user": USER, "password": PASSWORD, "grt": token}
    resp = s1.post(LOGIN_URL, data=payload, headers={**HEADERS, "Referer": LOGIN_URL}, timeout=15, allow_redirects=True)
    with open("debug_pos_login.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    exit()

sep("ABORDAGEM 2: home → /members-area → /login")
s2 = requests.Session()
s2.get(BASE_URL, headers=HEADERS, timeout=15)
s2.get(f"{BASE_URL}/members-area", headers={**HEADERS, "Referer": BASE_URL}, timeout=15)
mostrar_cookies(s2)

r2 = s2.get(LOGIN_URL, headers={**HEADERS, "Referer": f"{BASE_URL}/members-area"}, timeout=15)
token2 = extrair_token(r2.text)

if token2:
    sep("TOKEN ENCONTRADO! Tentando login...")
    payload = {"user": USER, "password": PASSWORD, "grt": token2}
    resp2 = s2.post(LOGIN_URL, data=payload, headers={**HEADERS, "Referer": LOGIN_URL}, timeout=15, allow_redirects=True)
    with open("debug_pos_login.html", "w", encoding="utf-8") as f:
        f.write(resp2.text)
    exit()

sep("ABORDAGEM 3: POST direto sem token CSRF")
s3 = requests.Session()
s3.get(BASE_URL, headers=HEADERS, timeout=15)
payload3 = {"user": USER, "password": PASSWORD}
r3 = s3.post(LOGIN_URL, data=payload3, headers={**HEADERS, "Referer": LOGIN_URL}, timeout=15, allow_redirects=True)

sep("RESUMO: nenhuma abordagem retornou token válido")
with open("debug_login_page.html", "w", encoding="utf-8") as f:
    f.write(r.text)
