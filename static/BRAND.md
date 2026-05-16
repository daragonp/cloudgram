# CloudGram · Brand Assets

Logo oficial de CloudGram. Inspirado en el lenguaje visual de Apple:
squircle iOS-style, gradiente `Apple Blue → Apple Purple`, profundidad
con highlight superior y sombra inferior.

## 🎨 Paleta

| Token        | HEX       | Uso                                       |
|--------------|-----------|-------------------------------------------|
| `--blue`     | `#0A84FF` | Inicio del gradiente (Apple System Blue)  |
| `--purple`   | `#BF5AF2` | Fin del gradiente (Apple System Purple)   |
| `--white`    | `#FFFFFF` | Nube central                              |
| `--ink`      | `#0e0e10` | Wordmark en light                         |
| `--paper`    | `#f5f5f7` | Wordmark en dark                          |

Dirección del gradiente: **135°** (top-left → bottom-right).

## 📐 Archivos incluidos

### Vectoriales (SVG) — escalables sin pérdida
| Archivo                    | Tamaño | Uso recomendado                         |
|----------------------------|--------|-----------------------------------------|
| `logo.svg`                 | 3 KB   | App icon en cualquier tamaño            |
| `logo-lockup.svg`          | 2 KB   | Logo + wordmark para light backgrounds  |
| `logo-lockup-dark.svg`     | 2 KB   | Logo + wordmark para dark backgrounds   |
| `favicon.svg`              | 1 KB   | Favicon moderno (browsers ≥ 2018)       |

### Raster (PNG) — para plataformas que no soportan SVG
| Archivo                          | Tamaño   | Uso                                     |
|----------------------------------|----------|-----------------------------------------|
| `logo-1024.png`                  | 1024×1024| App Store / Play Store                  |
| `logo-512.png`                   | 512×512  | Telegram Bot Profile Picture            |
| `logo-256.png`                   | 256×256  | OG/Twitter card                         |
| `logo-192.png`                   | 192×192  | PWA manifest                            |
| `logo-180.png` / `apple-touch-icon.png` | 180×180 | iOS home-screen                  |
| `logo-152.png`                   | 152×152  | iPad legacy                             |
| `logo-120.png`                   | 120×120  | iPhone legacy                           |
| `logo-lockup-light-2400.png`     | 2400×667 | Hero light                              |
| `logo-lockup-dark-2400.png`      | 2400×667 | Hero dark                               |
| `favicon.ico`                    | multi    | Favicon legacy (Edge/IE/Firefox viejo)  |

## ✅ Normas de uso

1. **Respeta el clearspace**: deja al menos `1× radio del squircle` de espacio libre alrededor del logo.
2. **No deformes** el aspect ratio: el icono es siempre cuadrado.
3. **No cambies los colores**: usa siempre el gradiente oficial.
4. **No añadas sombras externas** (la sombra ya está horneada dentro del SVG).
5. **Mínimo de visualización**: 24×24 px para el icono solo, 96×24 px para el lockup.

## 🚀 Cómo aplicarlo

### En el panel web (ya está integrado)
```html
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<img src="/static/logo.svg" alt="CloudGram" width="32" height="32">
```

### En Telegram (foto de perfil del bot)
1. Abre `@BotFather` → `/setuserpic` → elige tu bot
2. Sube `logo-512.png`

### En GitHub README
```markdown
<img src="static/logo-lockup-dark.svg#gh-dark-mode-only" height="80">
<img src="static/logo-lockup.svg#gh-light-mode-only" height="80">
```

### Como Open Graph image
```html
<meta property="og:image" content="https://tudominio.com/static/logo-256.png">
<meta property="twitter:image" content="https://tudominio.com/static/logo-256.png">
```
