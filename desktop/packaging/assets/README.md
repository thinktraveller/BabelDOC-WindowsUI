# BabelDOC Windows 图标

`babeldoc.ico` 由本仓库原有的 `docs/images/babeldoc-small-logo-with-transparent-background.png`（1024 × 1024、透明背景）生成。原图也用于 `mkdocs.yml` 的站点 logo/favicon（同名 SVG）；原始资产保持原位，不作修改。该资产可追溯到仓库提交 `ec5e3a8ac8c923aa6645d363ae1ed01f82bacda4`。

生成使用 Pillow，将原图保存为包含 16、24、32、48、64、128、256 像素图层的 Windows ICO。图标在 `babeldoc.spec` 中用于 PyInstaller EXE，在 `installer.iss` 中用于安装程序；安装后的快捷方式使用 EXE 中嵌入的图标。
