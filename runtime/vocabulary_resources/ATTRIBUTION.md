# 词汇资源署名与许可证

本目录包含与程序代码分离的第三方词典数据。`dictionary.sqlite3` 是派生数据
资源，不因与本项目一同分发而改用本项目代码许可证。

## 英汉词典

`dictionary.sqlite3` 从 English Wiktionary 的 Kaikki/Wiktextract 机器可读快照中
提取，原始快照来源与校验值记录在 `MANIFEST.json`。

- 内容来源：Wiktionary contributors，English Wiktionary
- 数据提供与后处理：Kaikki.org / Tatu Ylonen
- 抽取工具：Wiktextract，Tatu Ylonen
- 原始数据页：https://kaikki.org/dictionary/rawdata.html
- English Wiktionary 版权页：https://en.wiktionary.org/wiki/Wiktionary:Copyrights
- 数据许可证：Creative Commons Attribution-ShareAlike 4.0 International
- 完整许可证：`LICENSES/CC-BY-SA-4.0.txt`
- Wiktextract 程序许可证：`LICENSES/wiktextract-MIT.txt`

本项目对原始内容做了修改：只保留可由项目 tokenizer 产生的英文 token；只保留
Mandarin/Chinese 翻译并排除源数据明确标注的非普通话方言项；进行 Unicode NFC、
大小写折叠、空白与部分繁简并列值清理、稳定去重和长度限制；并仅沿数据中明确、
唯一的词形关系建立一跳 `form_of` 回退。派生词典按 CC BY-SA 4.0 继续提供。
未经猜测生成 lemma，也未使用在线翻译或 AI。
