import re, hashlib, os
# 使い方：リポジトリのいちばん上で  python3 dev/build.py   （dev フォルダの中で python3 build.py でも同じ）
#   dev/ui2.html（画面のもと）＋ dev/core.js（計算）＋ data/latest.json（保存しておくデータ）→ いちばん上の index.html を作る
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE) if os.path.basename(HERE) == "dev" else HERE
sys.path.insert(0, HERE)
os.chdir(ROOT)
ui=open(os.path.join(HERE,'ui2.html'),encoding='utf-8').read()
core=open(os.path.join(HERE,'core.js'),encoding='utf-8').read()
snap=open('data/latest.json',encoding='utf-8').read().replace('</','<\\\\/')
html=ui.replace('__CORE__',core).replace('__SNAPSHOT__',snap).replace('__BUILD__',__import__('datetime').datetime.now().strftime('%Y%m%d%H%M%S'))

# アイコン・演出の絵・起動画面の画像：URLに中身の目印（?v=中身から作った8文字）を付ける。
# 絵を変えると目印も変わるので、スマホに残っている前の絵が使われない（前のアイコンがチラッと出ない）
def ver(m):
    attr, path = m.group(1), m.group(2)
    f = path.split('?')[0]
    if not os.path.exists(f):
        return m.group(0)
    h = hashlib.md5(open(f, 'rb').read()).hexdigest()[:8]
    return f'{attr}="{f}?v={h}"'
html=re.sub(r'(href|src)="((?:splash/)?[A-Za-z0-9_\-]+\.(?:png|jpg)(?:\?v=[0-9a-f]+)?)"', ver, html)
# パワプロ風（夜）：パワプロ風（昼）のスタイルから夜の色の指定を作って、いちばん最後のスタイルの前に入れる
import pawa_night
_css = ''.join(re.findall(r'<style[^>]*>(.*?)</style>', ui, re.S))
_night = pawa_night.build(_css)
# スタイリッシュ（ライト）：スタイリッシュ（黒）のスタイルから明るい色の指定を作る
import sty_light
_night += "\n/* ===== スタイリッシュ（ライト）：自動で作った色の置き換え（sty_light.py） ===== */\n" + sty_light.build(_css)
# 4つの見た目の文字の読みやすさ：できあがったスタイル全体（自動で作った夜・ライトも含む）から、足りない文字の色を直す
import contrast_fix
_all = _css + "\n" + _night
_night += "\n/* ===== 文字の読みやすさ（contrast_fix.py） ===== */\n" + contrast_fix.build(_all)
# 自動で作った指定は、手で書いた夜・ライトの仕上げ（「===== パワプロ風（夜）：パワプロの形」から下）より前に入れる。
# 同じ強さなら手で書いた仕上げが勝つように（自動の指定が仕上げを打ち消さないように）
_mark = html.find('/* ===== パワプロ風（夜）：パワプロの形')
_i = _mark if _mark > 0 else html.rindex('</style>')
html = html[:_i] + "\n/* ===== パワプロ風（夜）：自動で作った色の置き換え（pawa_night.py） ===== */\n" + _night + "\n" + html[_i:]
open('index.html','w',encoding='utf-8').write(html)

# manifest.json のアイコンにも同じ目印を付ける
import json
mf=json.load(open('manifest.json',encoding='utf-8'))
for ic in mf.get('icons',[]):
    f=ic['src'].split('?')[0]
    if os.path.exists(f):
        ic['src']=f+'?v='+hashlib.md5(open(f,'rb').read()).hexdigest()[:8]
open('manifest.json','w',encoding='utf-8').write(json.dumps(mf,ensure_ascii=False,indent=2)+"\n")

# 自動更新（status.js）が使う計算ロジックも同じものにそろえる
import shutil
shutil.copyfile(os.path.join(HERE, "core.js"), "scripts/core.js")
