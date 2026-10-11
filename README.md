# tmux-osc7501

tmux'ta, her pane'de çalışan programın durumunu gösterir: çalışıyor, onay bekliyor, bitti, hata. Durum bilgisi [OSC 7501 (Program Status Protocol)](https://superlogical.com/rex/docs/build/program-status) ile gelir. Bu protokol Superlogical'ın Rex terminali için yazdığı genel bir spesifikasyon ve henüz taslak (sürüm 0.4, Ekim 2026).

```
 0:api 0● 1! 3✓   1:frontend ! claude   2:infra ✓ terraform   3:zsh
```

- `●` sarı: çalışıyor (varsa yüzde de gösterilir)
- `!` kırmızı: senden onay, cevap veya giriş bekliyor
- `✗` kırmızı: hata verdi
- `✓` yeşil: bitti, sonucuna henüz bakmadın

Tek pane'li bir pencerede durum, program adıyla birlikte gösterilir (`! claude`). Birden çok pane'i olan pencerede, durum bildiren her pane için pane numarası ve simgesi gösterilir (`0● 1! 3✓`). İstersen durum her pane'in üst kenarında da görünür (bkz. Pane kenarlığı).

Bir pane'e geçtiğinde (klavyeyle ya da fareyle) o pane'deki `✓` ve `✗` kaybolur.

Bakmadığın bir pane'de bir program onay beklemeye başladığında, işini bitirdiğinde ya da hata verdiğinde tmux mesaj satırında kısa bir bildirim çıkar: `[1.1:frontend] claude blocked permission Claude needs your permission to use Bash`. Aynı penceredeki komşu pane'ler de buna dahil.

`prefix + A` ile yalnızca durum bildiren pane'leri gösteren bir liste açılır. Bu listeyi bir "ajan gelen kutusu" gibi kullanabilirsin: seçtiğin pane'e atlarsın.

## Pane mi, pencere mi?

Varsayılan kapsam `pane`: her pane ayrı izlenir. Tek pencerede birkaç pane'le çalışıyorsan (örneğin dört Claude Code oturumu) doğru seçim budur.

Her pencerede tek bir iş yürütüyorsan eski davranışı seçebilirsin:

```tmux
set -g @osc7501-scope window
```

| | `pane` (varsayılan) | `window` |
|---|---|---|
| Status bar | Her pane'in simgesi, tek pane'de etiket | Penceredeki en önemli durumun etiketi |
| `✓`/`✗` ne zaman silinir | Pane'i seçince ya da penceresine geçince o an aktif olan pane'de | Pencereye geçince tüm pane'lerde |
| Bildirim ne zaman çıkmaz | O pane'e zaten bakıyorsan | O pencereye zaten bakıyorsan |
| `prefix + A` | Pane'leri listeler | Pencereleri listeler |

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
                                  window-status-format, pane kenarlığı, bildirim
```

- **İzleyici:** Her pane'in ham çıktısı `pipe-pane -O` ile küçük bir Python izleyiciye akar. İzleyici, spesifikasyona göre kayıt tutar. Uygulanan kurallar: 4096 bayt sınırı, hiyerarşik `id`'ler, `clear`, `app` mirası, en fazla 256 kayıt, RIS (`ESC c`) ile tüm kayıtları silme, yeni shell prompt'u (OSC 133;A) geldiğinde `working`/`blocked` kayıtlarını düşürme.
- **Pane etiketi:** Her izleyici yalnızca kendi pane'inin etiketini ve simgesini yazar. Status bar bunları `#{P:}` döngüsüyle okur, böylece iki pane aynı anda değişse de birbirinin yazdığını ezmez.
- **Pencere özeti:** Bir pencerede birden fazla pane varsa en önemli durum ayrıca pencere seçeneklerine yazılır. Öncelik sırası: `blocked` > `error` > `working` > `done` > `idle`. Kapanan ya da başka pencereye taşınan bir pane, eski penceresinde etiket bırakmaz.
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

Claude Code, OSC 7501'i kendiliğinden göndermiyor. Bu işi Claude Code'un hook'ları yapar. Eklentiyi son yerine (`~/.tmux/plugins/...`) koyduktan **sonra** kur, çünkü hook'lar komutun o anki tam yolunu kaydeder:

```sh
~/.tmux/plugins/tmux-osc7501/bin/osc7501 install-claude-hooks
```

Bu komut hook'ları `~/.claude/settings.json` dosyasına ekler. Mevcut hook'lara dokunmaz ve iki kez çalıştırılırsa aynı hook'ları tekrar eklemez. Değiştirmeden önce dosyanın yedeğini `settings.json.osc7501-<tarih>.bak` adıyla alır; eski yedeklerin üzerine yazmaz. Önce ne yazacağını görmek istersen `--dry-run` ekle. Elle kurmak istersen örnek ayarlar `examples/claude-settings.json` dosyasında.

Kurulumdan sonra açık Claude Code oturumlarını yeniden başlat.

Hook'ların gönderdiği durumlar:

| Claude Code olayı | Gönderilen durum |
|---|---|
| `SessionStart` | `idle` |
| `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure` | `working` |
| `PermissionRequest` | `blocked`, `kind=permission`: izin penceresi açıldığı anda |
| `PreToolUse` (`AskUserQuestion`) | `blocked`, `kind=question`, mesaj olarak sorunun kendisi |
| `PreToolUse` (`ExitPlanMode`) | `blocked`, `kind=permission`: plan onayı |
| `Notification` (`permission_prompt`) | `blocked`, `kind=permission` (izin penceresi ~6 sn beklediyse yedek olarak) |
| `Notification` (MCP formu, arka plan ajanının sorusu, kota sonrası Enter beklemesi) | `blocked`, `kind=question` |
| `Notification` (`quota_auto_resume_fired`) | `working` |
| `Notification` (diğer tüm tipler, örneğin `idle_prompt`) | yok sayılır |
| `Stop` | `done` |
| `StopFailure` (API hatası, örneğin rate limit) | `error`, mesaj olarak hata metni |
| `SessionEnd` | `clear` |

Hook'lar arka planda (`"async": true`) çalışmaz, çünkü Claude Code arka plan hook'larının sırasını garanti etmiyor: geç kalan bir `PostToolUse`, `Stop`'un yazdığı `done`'ı `working`'e geri çevirebilirdi. Bunun bedeli, her araç çağrısından sonra yaklaşık 70 ms.

Claude Code hook'ları kontrol terminali olmadan çalıştırır. Bu yüzden hook, diziyi `$TMUX_PANE` üzerinden pane'in tty'sine yazar. Yani Claude Code hook'ları yalnızca tmux içinde durum bildirir. Hook'un stdout'una hiçbir şey basılmaz, çünkü Claude Code bazı olaylarda stdout'u modele gönderir. Hook her durumda 0 koduyla çıkar, böylece Claude Code'u asla bozmaz.

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

`osc7501` bir tmux eklentisi değil, düz bir CLI'dır. Shell'den çalıştırıldığında diziyi kendi terminaline yazar, bu yüzden OSC 7501'i destekleyen bir terminalde (örneğin Rex) tmux olmadan da çalışır. Protokolü tanımayan terminaller bu diziyi sessizce yok sayar.

## Ayarlar

Ayarları `~/.tmux.conf` dosyasında, `run-shell` satırından **önce** tanımla. İzleyiciler etiket ayarlarını başlarken okur; çalışan bir oturumda bunları değiştirirsen değişiklik, izleyiciler yeniden başlatılınca geçerli olur (bkz. Bilinen sınırlar). `@osc7501-scope`, `@osc7501-pane-border` ve `@osc7501-inbox-key` için `tmux source ~/.tmux.conf` yeterlidir.

| Seçenek | Varsayılan | Açıklama |
|---|---|---|
| `@osc7501-scope` | `pane` | `pane` ya da `window` (bkz. Pane mi, pencere mi?) |
| `@osc7501-format-working` | `#[fg=yellow]● {app} {progress}#[default]` | Etiket (tek pane'li pencere, pane kenarlığı, `window` kapsamı) |
| `@osc7501-format-blocked` | `#[fg=red,bold]! {app}#[default]` | |
| `@osc7501-format-error` | `#[fg=red]✗ {app}#[default]` | |
| `@osc7501-format-done` | `#[fg=green]✓ {app}#[default]` | |
| `@osc7501-format-idle` | *(boş)* | |
| `@osc7501-icon-working` | `#[fg=yellow]●#[default]` | Çok pane'li pencerede pane simgesi |
| `@osc7501-icon-blocked` | `#[fg=red,bold]!#[default]` | |
| `@osc7501-icon-error` | `#[fg=red]✗#[default]` | |
| `@osc7501-icon-done` | `#[fg=green]✓#[default]` | |
| `@osc7501-icon-idle` | *(boş)* | |
| `@osc7501-app-aliases` | `claude-code=claude` | Etikette program adını kısaltır: `ad=kısa,ad2=kısa2` |
| `@osc7501-label-max` | `30` | `{msg}` ve `{title}` için azami uzunluk |
| `@osc7501-notify` | `blocked,error,done` | Hangi durumlarda bildirim çıksın; `off` ile kapatılır |
| `@osc7501-notify-format` | `[#{window_index}#{?#{!=:#{window_panes},1},.#{pane_index},}:#{window_name}] {app} {state} {kind} {msg}` | Bildirim metni |
| `@osc7501-notify-duration` | `5000` | Bildirimin ekranda kalma süresi (ms) |
| `@osc7501-clear-on-visit` | `on` | Pane'e geçince `done`/`error` işaretleri silinsin mi |
| `@osc7501-inbox-key` | `A` | Liste için tuş (`prefix + A`); `off` ile kapatılır |
| `@osc7501-status-append` | `on` | Etiket `window-status-format`'ın sonuna eklensin mi |
| `@osc7501-pane-border` | `off` | `on`: durum pane kenarlığında da görünür |
| `@osc7501-python` | `python3` | Python yolu |

Yer tutucular: `{app}`, `{state}`, `{kind}`, `{progress}`, `{msg}`, `{title}`. Boş kalan bir yer tutucu, önündeki boşlukla birlikte silinir. Bildirim metninde tmux formatlarını da kullanabilirsin.

### Pane kenarlığı

```tmux
set -g @osc7501-pane-border on
```

Bu ayar `pane-border-status` kapalıysa onu `top` yapar ve etiketi `pane-border-format`'ın sonuna ekler. tmux 3.7'de fare açıksa varsayılan kenarlığın sağında `[z][x]` düğmeleri vardır; etiket onların arkasına düşer ve dar pane'lerde görünmeyebilir. Etiketi pane numarasının yanına almak için kendi formatını yaz (bu, düğmeleri de kaldırır):

```tmux
set -g pane-border-format '#{?pane_active,#[reverse],}#{pane_index}#[default]#{osc7501} "#{pane_title}"'
```

Claude Code pane başlığına o anki işin konusunu yazdığı için kenarlıkta şöyle bir satır görürsün: `2 ! claude "✳ refactor-auth"`.

**Kenarlık rengi durumu göstersin:** onay bekleyen ya da hata veren pane kırmızı, çalışan sarı, biten yeşil, boştaki gri; seçili pane hep camgöbeği.

```tmux
set -g pane-border-style '#{?#{||:#{==:#{@osc7501_state},blocked},#{==:#{@osc7501_state},error}},fg=red,#{?#{==:#{@osc7501_state},working},fg=yellow,#{?#{==:#{@osc7501_state},done},fg=green,fg=colour240}}}'
set -g pane-active-border-style 'fg=cyan'
```

### Etiketin yeri

Etiketin nerede duracağını seçmek istersen kendi formatına `#{osc7501}` yaz. Bu, status bar'da ve pane kenarlığında çalışır:

```tmux
set -g window-status-format '#I:#W#{osc7501}'
set -g window-status-current-format '#[bold]#I:#W#{osc7501}'
set -g pane-border-format ' #{pane_index} #{pane_title}#{osc7501} '
```

Eklenti `#{osc7501}`'i status bar'da `#{E:@osc7501_status}`, pane kenarlığında `#{E:@osc7501_pane_status}` ile değiştirir. Eski sürümün eklediği etiket de yeniden yüklemede bu yeni biçime çevrilir.

**Kendi formatında kullanabileceğin seçenekler:**

- Pane seviyesi: `#{@osc7501_state}`, `#{@osc7501_app}`, `#{@osc7501_kind}`, `#{@osc7501_msg}`, `#{@osc7501_progress}`, `#{@osc7501_title}`, `#{@osc7501_pane_label}`, `#{@osc7501_pane_icon}`
- Pencere seviyesi: `#{@osc7501_label}`, `#{@osc7501_window_state}`, `#{@osc7501_window_app}`, `#{@osc7501_window_msg}`, `#{@osc7501_window_pane}`

## Bilinen sınırlar

- **Pane başına tek pipe:** tmux her pane'de yalnızca bir `pipe-pane`'e izin verir. Pane zaten pipe ediliyorsa (örneğin tmux-logging ile), eklenti o pane'i atlar ve diğer pipe'ı bozmaz.
- **Bellek:** Her pane için bir izleyici çalışır ve her biri yaklaşık 13 MB RAM kullanır.
- **Config yeniden yüklemesi:** `tmux source` çalışan izleyicileri yeniden başlatmaz. Eklentinin kodunu güncellediysen önce `pkill -f osc7501_watch.py`, sonra `tmux source ~/.tmux.conf` çalıştır.
- **`prefix ;` (last-pane):** tmux'ta bu komut için bir `after-` hook'u yok. Bu tuşla döndüğün pane'deki `✓`/`✗`'nin silinmesi için `set -g focus-events on` gerekir. Pane'i seçme, fareyle tıklama ve pencere değiştirme bu ayar olmadan da çalışır.
- **Zaten içinde olduğun pane:** tmux, aktif pane yeniden seçildiğinde (örneğin içine tekrar tıkladığında) hook çalıştırmıyor. İçinde olduğun pane'de beliren `✓`, bir sonraki istekte ya da başka bir pane'e geçip döndüğünde kalkar.
- **Esc ile durdurma:** Claude'u Esc ile durdurduğunda Claude Code `Stop` hook'unu çalıştırmıyor. Bu yüzden etiket bir sonraki isteğe kadar `●` olarak kalır.
- **Onaydan sonra uzun süren araç:** Bir izni onayladıktan sonra araç uzun sürerse (örneğin 5 dakikalık bir build), araç bitene kadar `!` görünür. Bunun sebebi, onay anında çalışan bir Claude Code hook'u olmaması; durum `PostToolUse` ile güncelleniyor.
- **Paralel araç çağrıları:** Claude birkaç aracı aynı anda çalıştırırken biri izin bekliyorsa, öteki araçların `PostToolUse`'u `!`'i geçici olarak `●`'ye çevirebilir. İzin penceresi 6 saniye açık kalırsa gelen `permission_prompt` bildirimi `!`'i geri getirir.
- **Pane'e dışarıdan yazma:** Hook, diziyi Claude Code'un çizdiği ekranla aynı tty'ye yazar. Claude Code'un bunun için önerdiği `terminalSequence` yolu yalnızca OSC 0/1/2/9/99/777'ye izin verdiği için OSC 7501'i taşıyamıyor. Dizi kısa ve tek bir `write` ile gittiği için pratikte sorun çıkmaz.
- **Taşınan pane (`window` kapsamı):** `join-pane` ya da `break-pane` ile taşınan bir pane'in eski penceresindeki etiket, o pane bir sonraki durum bildirimini yapana kadar kalır. `pane` kapsamında status doğrudan pane'lerden okunduğu için bu olmaz.
- **Sorgu yanıtı:** Eklenti `OSC 7501 ; ?` sorgusuna cevap vermez. tmux, cihaz sorgusuna (DA) eklentiden önce cevap verdiği için verilecek cevap zaten işe yaramazdı. Spesifikasyon, programların sorgu yapmadan durum göndermesine izin veriyor.
- **Standart değişebilir:** OSC 7501 henüz bir taslak; spesifikasyon değişirse izleyicinin de güncellenmesi gerekir.

## Testler

```sh
python3 tests/test_watch.py         # parser, kayıtlar, akış tarayıcı, etiket/bildirim mantığı
python3 tests/test_cli.py           # Claude Code hook eşlemesi ve kurulum
python3 tests/integration_test.py   # gerçek tmux sunucusuyla uçtan uca testler (ayrı soket)
```

## Kaldırma

`~/.tmux.conf` dosyasından `run-shell` satırını sil, sonra şunu çalıştır:

```sh
for h in after-new-window after-split-window session-created session-window-changed \
         client-session-changed after-select-pane pane-focus-in; do
  tmux set-hook -gu "$h[7501]"
done
pkill -f osc7501_watch.py
tmux source ~/.tmux.conf
```

Claude Code hook'larını kaldırmak için `~/.claude/settings.json` dosyasından `osc7501 claude-hook` içeren girdileri sil.
