# tmux-osc7501

tmux status bar'ında, her pencerede çalışan programın durumunu gösterir: çalışıyor, onay bekliyor, bitti, hata. Durum bilgisi [OSC 7501 (Program Status Protocol)](https://superlogical.com/rex/docs/build/program-status) ile gelir. Bu protokolü Mitchell Hashimoto önerdi ve şimdilik bir taslak.

```
 0:api ● claude   1:frontend ! claude   2:infra ✓ terraform   3:zsh
```

- `●` sarı: çalışıyor (varsa yüzde de gösterilir)
- `!` kırmızı: senden onay, cevap veya giriş bekliyor
- `✗` kırmızı: hata verdi
- `✓` yeşil: bitti, sonucuna henüz bakmadın

Pencereye geçtiğinde `✓` ve `✗` kaybolur.

Arka plandaki bir pencerede bir program onay beklemeye başladığında, işini bitirdiğinde ya da hata verdiğinde tmux mesaj satırında kısa bir bildirim çıkar: `[1:frontend] claude blocked permission Claude needs your permission to use Bash`.

`prefix + A` ile yalnızca durum bildiren pencereleri gösteren bir liste açılır. Bu listeyi bir "ajan gelen kutusu" gibi kullanabilirsin: seçtiğin pencereye atlarsın.

## Nasıl çalışır

tmux, tanımadığı OSC dizilerini yutar ve dış terminale iletmez. Bu eklenti diziyi tmux'un içinde, kaybolmadan önce yakalar:

```
program ──(OSC 7501)──▶ pane pty ──▶ tmux ──▶ ekran
                                     │
                                     └─ pipe-pane -O ──▶ osc7501_watch.py
                                                           │  (her pane için bir tane)
                                                           ▼
                                        @osc7501_* pane/pencere seçenekleri
                                                           ▼
                                            window-status-format, bildirim
```

- **İzleyici:** Her pane'in ham çıktısı `pipe-pane -O` ile küçük bir Python izleyiciye akar. İzleyici, spesifikasyona göre kayıt tutar. Uygulanan kurallar: 4096 bayt sınırı, hiyerarşik `id`'ler, `clear`, `app` mirası, en fazla 256 kayıt, RIS (`ESC c`) ile tüm kayıtları silme, yeni shell prompt'u (OSC 133;A) geldiğinde `working`/`blocked` kayıtlarını düşürme.
- **Pencere özeti:** Bir pencerede birden fazla pane varsa en önemli durum seçilir. Öncelik sırası: `blocked` > `error` > `working` > `done` > `idle`.
- **SSH:** Uzak makinede çalışan bir program da durum bildirebilir. Dizi, SSH üzerinden yerel pane'e kadar gelir.

## Gereksinimler

- tmux 3.2 veya üstü
- python3 (3.8+). Ek paket gerekmez.

## Kurulum

**Elle kurulum:**

```sh
git clone <repo-adresin> ~/.tmux/plugins/tmux-osc7501   # ya da zip'i bu klasöre aç
```

`~/.tmux.conf` dosyasına, tema eklentilerinden **sonra** gelecek şekilde şu satırı ekle:

```tmux
run-shell ~/.tmux/plugins/tmux-osc7501/osc7501.tmux
```

**TPM ile kurulum:** Repoyu GitHub'a koyduysan `set -g @plugin '<kullanıcı>/tmux-osc7501'` satırını ekle ve `prefix + I` ile kur.

Kurulumdan sonra ayarları yeniden yükle: `tmux source ~/.tmux.conf`

### Claude Code

Claude Code, OSC 7501'i kendiliğinden göndermiyor. Bu işi Claude Code'un hook'ları yapar. Kurmak için:

```sh
~/.tmux/plugins/tmux-osc7501/bin/osc7501 install-claude-hooks
```

Bu komut hook'ları `~/.claude/settings.json` dosyasına ekler. Mevcut hook'lara dokunmaz, eski dosyanın `.bak` uzantılı bir yedeğini bırakır ve iki kez çalıştırılırsa aynı hook'ları tekrar eklemez. Önce ne yazacağını görmek istersen `--dry-run` ekle. Elle kurmak istersen örnek ayarlar `examples/claude-settings.json` dosyasında.

Kurulumdan sonra açık Claude Code oturumlarını yeniden başlat.

Hook'ların gönderdiği durumlar:

| Claude Code olayı | Gönderilen durum |
|---|---|
| `SessionStart` | `idle` |
| `UserPromptSubmit`, `PostToolUse` | `working` |
| `Notification` (izin isteği) | `blocked`, `kind=permission`, mesaj olarak Claude'un sorusu |
| `Notification` ("waiting for your input") | yok sayılır, `done` gösterilmeye devam eder |
| `Stop` | `done` |
| `SessionEnd` | `clear` |

Hook, diziyi `/dev/tty`'ye yazar. Hook'un stdout'una hiçbir şey basılmaz, çünkü Claude Code bazı olaylarda stdout'u modele gönderir. Hook'un kontrol terminali yoksa, `$TMUX_PANE` üzerinden pane'in tty'sine yazar. Hook her durumda 0 koduyla çıkar, böylece Claude Code'u asla bozmaz.

## Kendi komutların için

```sh
osc7501 run -- make test          # çalışırken ●, bitince ✓ ya da ✗
osc7501 run -a deploy -- ./deploy.sh

osc7501 working -a build -p 40    # elle durum gönderme
osc7501 blocked -a deploy -k question -m "Prod'a çıkayım mı?"
osc7501 done -a build
osc7501 clear
```

Bunları bir shell script'e eklemek istersen tek satır yeter:

```sh
printf '\e]7501;state=%s:app=%s\e\\' working mybuild
```

`osc7501` bir tmux eklentisi değil, düz bir CLI'dır. Ghostty gibi OSC 7501'i destekleyen bir terminalde tmux olmadan da çalışır. Protokolü tanımayan terminaller bu diziyi sessizce yok sayar.

## Ayarlar

Ayarları `~/.tmux.conf` dosyasında, `run-shell` satırından **önce** tanımla. İzleyiciler ayarları başlarken okur; çalışan bir oturumda bir ayarı değiştirirsen değişiklik, izleyiciler yeniden başlatılınca geçerli olur (bkz. Bilinen sınırlar).

| Seçenek | Varsayılan | Açıklama |
|---|---|---|
| `@osc7501-format-working` | `#[fg=yellow]● {app} {progress}#[default]` | Status bar etiketi |
| `@osc7501-format-blocked` | `#[fg=red,bold]! {app}#[default]` | |
| `@osc7501-format-error` | `#[fg=red]✗ {app}#[default]` | |
| `@osc7501-format-done` | `#[fg=green]✓ {app}#[default]` | |
| `@osc7501-format-idle` | *(boş)* | |
| `@osc7501-app-aliases` | `claude-code=claude` | Etikette program adını kısaltır: `ad=kısa,ad2=kısa2` |
| `@osc7501-label-max` | `30` | `{msg}` ve `{title}` için azami uzunluk |
| `@osc7501-notify` | `blocked,error,done` | Hangi durumlarda bildirim çıksın; `off` ile kapatılır |
| `@osc7501-notify-format` | `[#{window_index}:#{window_name}] {app} {state} {kind} {msg}` | Bildirim metni |
| `@osc7501-notify-duration` | `5000` | Bildirimin ekranda kalma süresi (ms) |
| `@osc7501-clear-on-visit` | `on` | Pencereye geçince `done`/`error` işaretleri silinsin mi |
| `@osc7501-inbox-key` | `A` | Liste için tuş (`prefix + A`); `off` ile kapatılır |
| `@osc7501-status-append` | `on` | Etiket `window-status-format`'ın sonuna eklensin mi |
| `@osc7501-python` | `python3` | Python yolu |

Yer tutucular: `{app}`, `{state}`, `{kind}`, `{progress}`, `{msg}`, `{title}`. Boş kalan bir yer tutucu, önündeki boşlukla birlikte silinir. Bildirim metninde tmux formatlarını da kullanabilirsin.

**Etiketin yeri:** Etiketin status bar'da nerede duracağını seçmek istersen kendi formatına `#{osc7501}` yaz:

```tmux
set -g window-status-format '#I:#W#{osc7501}'
set -g window-status-current-format '#[bold]#I:#W#{osc7501}'
```

**Kendi formatında kullanabileceğin seçenekler:**

- Pencere seviyesi: `#{@osc7501_label}`, `#{@osc7501_window_state}`, `#{@osc7501_window_app}`, `#{@osc7501_window_msg}`, `#{@osc7501_window_pane}`
- Pane seviyesi: `#{@osc7501_state}`, `#{@osc7501_app}`, `#{@osc7501_kind}`, `#{@osc7501_msg}`, `#{@osc7501_progress}`, `#{@osc7501_title}`

## Bilinen sınırlar

- **Pane başına tek pipe:** tmux her pane'de yalnızca bir `pipe-pane`'e izin verir. Pane zaten pipe ediliyorsa (örneğin tmux-logging ile), eklenti o pane'i atlar ve diğer pipe'ı bozmaz.
- **Bellek:** Her pane için bir izleyici çalışır ve her biri yaklaşık 13 MB RAM kullanır.
- **Config yeniden yüklemesi:** `tmux source` çalışan izleyicileri yeniden başlatmaz. Eklentinin kodunu güncellediysen önce `pkill -f osc7501_watch.py`, sonra `tmux source ~/.tmux.conf` çalıştır.
- **Esc ile durdurma:** Claude'u Esc ile durdurduğunda Claude Code `Stop` hook'unu çalıştırmıyor. Bu yüzden etiket bir sonraki isteğe kadar `●` olarak kalır.
- **Onaydan sonra uzun süren araç:** Bir izni onayladıktan sonra araç uzun sürerse (örneğin 5 dakikalık bir build), araç bitene kadar `!` görünür. Bunun sebebi, onay anında çalışan bir Claude Code hook'u olmaması; durum `PostToolUse` ile güncelleniyor.
- **Sorgu yanıtı:** Eklenti `OSC 7501 ; ?` sorgusuna cevap vermez. tmux, cihaz sorgusuna (DA) eklentiden önce cevap verdiği için verilecek cevap zaten işe yaramazdı. Spesifikasyon, programların sorgu yapmadan durum göndermesine izin veriyor.
- **Standart değişebilir:** OSC 7501 henüz bir taslak; spesifikasyon değişirse izleyicinin de güncellenmesi gerekir.

## Testler

```sh
python3 tests/test_watch.py         # parser, kayıtlar, akış tarayıcı
python3 tests/integration_test.py   # gerçek tmux sunucusuyla uçtan uca testler (ayrı soket)
```

## Kaldırma

`~/.tmux.conf` dosyasından `run-shell` satırını sil, sonra şunu çalıştır:

```sh
for h in after-new-window after-split-window session-created session-window-changed client-session-changed; do
  tmux set-hook -gu "$h[7501]"
done
pkill -f osc7501_watch.py
tmux source ~/.tmux.conf
```

Claude Code hook'larını kaldırmak için `~/.claude/settings.json` dosyasından `osc7501 claude-hook` içeren girdileri sil.
