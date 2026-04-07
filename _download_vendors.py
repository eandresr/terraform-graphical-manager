import urllib.request
import os
import re

os.makedirs('static/js/vendors', exist_ok=True)
os.makedirs('static/fonts/inter', exist_ok=True)
os.makedirs('static/fonts/jetbrains-mono', exist_ok=True)

UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/123.0.0.0 Safari/537.36'
)


def download(url, path, extra_headers=None):
    headers = {'User-Agent': UA}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    with open(path, 'wb') as f:
        f.write(data)
    print(f'  OK  {path}  ({len(data):,} bytes)')
    return data


print('=== Tailwind Play CDN ===')
download('https://cdn.tailwindcss.com', 'static/js/vendors/tailwind.cdn.js')

print('=== Alpine.js 3.14.3 ===')
download('https://unpkg.com/alpinejs@3.14.3/dist/cdn.min.js', 'static/js/vendors/alpine.min.js')

print('=== Socket.IO 4.6.1 ===')
download(
    'https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.6.1/socket.io.min.js',
    'static/js/vendors/socket.io.min.js',
)

print('=== Chart.js 4.4.4 ===')
download(
    'https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js',
    'static/js/vendors/chart.umd.min.js',
)

print('=== Google Fonts CSS ===')
fonts_url = (
    'https://fonts.googleapis.com/css2'
    '?family=Inter:wght@300;400;500;600;700'
    '&family=JetBrains+Mono:wght@400;500'
    '&display=swap'
)
css_bytes = download(fonts_url, '_fonts_tmp.css')
css_text = css_bytes.decode('utf-8')

# Extract all woff2 URLs from the CSS
woff2_urls = re.findall(
    r'url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)', css_text
)
print(f'  Found {len(woff2_urls)} font files')

font_map = {}
for url in woff2_urls:
    fname = url.split('/')[-1]
    # Find which family this URL belongs to by looking at preceding CSS context
    pos = css_text.find(url)
    context = css_text[max(0, pos - 500):pos]
    if 'JetBrains' in context:
        local_path = f'static/fonts/jetbrains-mono/{fname}'
        font_map[url] = f'../fonts/jetbrains-mono/{fname}'
    else:
        local_path = f'static/fonts/inter/{fname}'
        font_map[url] = f'../fonts/inter/{fname}'
    if not os.path.exists(local_path):
        download(url, local_path)
    else:
        print(f'  SKIP {local_path} (already exists)')

# Rewrite the CSS to reference local font files
local_css = css_text
for url, local in font_map.items():
    local_css = local_css.replace(url, local)

with open('static/css/fonts.css', 'w') as f:
    f.write(local_css)
print(f'  OK  static/css/fonts.css  ({len(local_css):,} bytes)')

# Clean up temp file
os.remove('_fonts_tmp.css')

print('\nAll vendor assets downloaded successfully.')
