# Bundled PDF font

The application bundles a static Regular (weight 400) instance of **Noto Sans SC**.
The font is embedded in generated PDFs and requires neither a network connection
nor Chinese fonts installed on Windows.

- Official Google Fonts source: <https://github.com/google/fonts/tree/main/ofl/notosanssc>
- Downloaded font: <https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf>
- Original license: <https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/OFL.txt>
- Upstream project: <https://github.com/notofonts/noto-cjk>
- Download date: 2026-09-24
- License: SIL Open Font License 1.1, reproduced unmodified in `OFL.txt`.
- Original copyright: Copyright 2014-2021 Adobe, with Reserved Font Name 'Source'.
- Downloaded variable font SHA-256: `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`
- Distributed static font SHA-256: `26305be85f5378dc7d53f3dfa6c852a7e46621b226423400c1821aca9fc01c41`

The static instance was generated using fontTools 4.66.0:

```text
python -m fontTools.varLib.instancer NotoSansSC-VF.ttf wght=400 --update-name-table --no-recalc-timestamp --output=NotoSansSC-Regular.ttf
```

No glyph subsetting was applied: the full upstream character set is retained.
Only the static font, this notice, and `OFL.txt` need to be included in the
released application. fontTools and the variable source are build tools/assets,
not runtime dependencies. No Microsoft font is redistributed.
