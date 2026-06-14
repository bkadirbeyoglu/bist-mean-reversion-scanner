# BIST Ortalamaya Dönüş (Mean Reversion) Tarayıcısı

Borsa İstanbul'da (BIST) EMA20 ve EMA50'den aşırı sapma gösteren hisseleri tespit eden ve sinyal sonrası 5 işlem gününü takip eden Python tabanlı bir tarama aracı.

## Nasıl Çalışır?

Tarayıcı, BIST100 veya BIST500 endeksindeki her hisse için EMA20 ve EMA50 hesaplar ve kapanış fiyatının her EMA'ya olan yüzdelik mesafesini ölçer:

```
gap% = (kapanış − EMA) / EMA × 100
```

Bu mesafe yapılandırılabilir bir eşiği aştığında sinyal üretilir:

| Sinyal | Anlam |
|---|---|
| `ABOVE_20` | Kapanış, EMA20'nin eşik% üstünde |
| `ABOVE_50` | Kapanış, EMA50'nin eşik% üstünde |
| `BELOW_20` | Kapanış, EMA20'nin eşik% altında |
| `BELOW_50` | Kapanış, EMA50'nin eşik% altında |

Bir hisse aynı anda birden fazla sinyal verebilir (ör. `BELOW_20+BELOW_50`).

Varsayılan eşikler: EMA20 için **%5**, EMA50 için **%8**.

## Sinyal Başına Özellikler

Her sinyal analiz için zengin bir veri seti ile kaydedilir:

| Özellik | Açıklama |
|---|---|
| `gap20_pct`, `gap50_pct` | EMA20 ve EMA50'ye yüzdelik mesafe |
| `atr20_pct` | 20 günlük ATR'nin kapanışa oranı (hissenin günlük volatilitesi) |
| `gap20_atr`, `gap50_atr` | ATR'ye normalize edilmiş gap (volatilite birimi cinsinden sapma) |
| `ema20_slope`, `ema50_slope` | EMA'nın 5 günlük değişim %'si (trend yönü) |
| `pre_momentum_5d` | Sinyal öncesi 5 günlük kümülatif getiri (düşüş/yükseliş hızı) |
| `vol_ratio` | Sinyal günü hacim / 20 günlük ortalama hacim |
| `rsi14` | 14 periyotluk RSI |
| `position` | Fiyatın konumu: Above both, Below both, Between |
| `above_count`, `below_count` | Sinyal yoğunluğu — aynı gün toplam ABOVE/BELOW sinyal sayısı |

## Sonuç Takibi

Her çalıştırmada, yeni taramadan önce önceki sinyallerin sonuç verileri otomatik olarak güncellenir. Her sinyal için 5 işlem günü boyunca şunlar takip edilir:

- **d1–d5**: Günlük kapanış % değişimi (sinyal kapanışından)
- **d1–d5 high/low %**: Gün içi en yüksek/düşük (sinyal kapanışına göre)
- **d1–d5 hacim oranı**: Günlük hacim / 20 günlük ortalama
- **max/min 5d**: 5 günlük penceredeki en iyi ve en kötü kapanış
- **XU100 bağlamı**: Sinyal günü ve d1'de endeks açılış/kapanış
- **at_limit**: Sinyal günü kapanışının BIST ±%10 tavan/taban sınırına ulaşıp ulaşmadığı

## Kurulum

```bash
git clone https://github.com/bkadirbeyoglu/bist-mean-reversion-scanner.git
cd bist-mean-reversion-scanner
pip install yfinance pandas
```

## Kullanım

### Tarama

```bash
# BIST100'ü varsayılan eşiklerle tara (EMA20: %5, EMA50: %8)
python bist_mean_reversion_scanner.py

# BIST500'ü tara
python bist_mean_reversion_scanner.py -i xu500

# Özel eşikler
python bist_mean_reversion_scanner.py -g 4 -G 7

# Belirli bir geçmiş seansı tara
python bist_mean_reversion_scanner.py -d 2026-06-01

# Kayıt tutmadan (sadece konsol çıktısı)
python bist_mean_reversion_scanner.py -n
```

### Argümanlar

| Kısa | Uzun | Varsayılan | Açıklama |
|---|---|---|---|
| `-i` | `--index` | `xu100` | Taranacak endeks: `xu100` veya `xu500` |
| `-g` | `--gap20` | `5.0` | EMA20 gap eşiği (%) |
| `-G` | `--gap50` | `8.0` | EMA50 gap eşiği (%) |
| `-d` | `--date` | son seans | Belirli bir seans tarihi (YYYY-MM-DD) |
| `-n` | `--no-log` | kapalı | CSV dosyalarına kayıt yapma |

### Endeks Bileşenlerinin Güncellenmesi

```bash
# BIST100 hisselerini KAP'tan güncelle
python update_index.py

# BIST500 hisselerini güncelle
python update_index.py -i xu500

# Yedek kaynak olarak Midas kullan
python update_index.py -i xu100 -s midas
```

## Çıktı Dosyaları

| Dosya | Açıklama |
|---|---|
| `mr_signals_xu100.csv` | Tüm özellikleri içeren sinyal kaydı |
| `mr_outcomes_xu100.csv` | d1–d5 verilerini içeren sonuç takibi |
| `mr_signals_xu500.csv` | BIST500 için aynısı |
| `mr_outcomes_xu500.csv` | BIST500 için aynısı |

## Önerilen İş Akışı

1. Tarayıcıyı her akşam borsa kapanışından sonra çalıştırın (BIST verileri yfinance'te kapanıştan yaklaşık 3–3,5 saat sonra, İstanbul saatiyle ~21:30 civarında güncellenir).
2. Gap ≥ %8 olan BELOW sinyallerini inceleyin.
3. Ertesi işlem günü: d1 kapanışını takip edin. d1 > %+1 ise giriş değerlendirin.
4. 5 işlem gününe kadar tutun.
5. Tarayıcı her çalıştırmada önceki sinyallerin sonuçlarını otomatik günceller — elle takip gerekmez.

## Veri Kaynakları

- **yfinance**: Hisse fiyat verileri (OHLCV), ~15 dakika gün içi gecikmesi, kapanış verileri kapanıştan ~3 saat sonra erişilebilir
- **KAP** (kap.org.tr): BIST endeks bileşenleri (`update_index.py` için birincil kaynak)
- **Midas** (getmidas.com): Endeks bileşenleri için yedek kaynak

## Gereksinimler

- Python 3.10+
- `yfinance`
- `pandas`

## Lisans

MIT

## Sorumluluk Reddi

Bu araç yalnızca araştırma ve eğitim amaçlıdır. Yatırım tavsiyesi niteliği taşımaz. Geçmiş performans gelecekteki sonuçları garanti etmez. Yatırım kararlarınızı vermeden önce kendi araştırmanızı yapın.
