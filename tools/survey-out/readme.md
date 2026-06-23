# Procedure

`/home/chris/Dropbox/projects/bkk/tools/survey-out/appendix1_variants.tsv` holds the variants defined in 通用规范汉字表, published 2013 in the PR China. We will use this to seed the canonicalization list for BKK as follows: Characters in the 'traditional' column become part of the character set.  Characters in the 'regulated' column will be replaced with the traditional version. In addition, the characters listed as variants will also be replaced by the traditional variant.

Additional variant mappings comes from `/home/chris/Dropbox/projects/bkk/tools/survey-out/Chinese Var-to-Rep_v1_0.tsv`. Please merge this with the above list to form 'bkk-variant-pairs.tsv' with the following format:

var_cp	var_char	reg_cp	reg_char	remarks

Characters should not occur in the var and reg column.  Such cases should be flagged in remarks.

The result is in 
`v2r-ge065.tsv`
now also augmented with pairs from twjp-vardb

Result of survey:

Shinji contradictions (24 total)

8 chain-resolved & added (xml_reg was itself a v2r variant — followed to its canonical):

shinji	xml said →	resolved to
壱	壹	一
挙	擧	舉
鶏	鷄	雞
鉱	鑛	礦
斎	齋	齊
渋	澁	澀
嬢	孃	娘
舗	舖	鋪
16 skipped — shinji char is already a v2r canonical (already "in the list" as standard, just not as a variant):
為, 飲, 衛, 回, 缶, 顔, 冊, 糸, 慎, 真, 竪, 鎮, 棟, 並, 褒, 謡

TW contradictions (21 total), with labels reversed (TW=var, JP=reg)

9 new added: 侉→誇, 產→産, 瘺→瘻, 絕→絶, 綠→緑, 銳→鋭, 錄→録, 閱→閲, 頹→頽
7 already match (reversed pair was already in v2r): 寘→置, 柰→奈, 玆→茲, 种→種, 秏→耗, 篱→籬, 缾→瓶
5 skipped — reversed-var is itself a v2r canonical (adding would demote a canonical): 啟, 眾, 缽, 說, 贗. Both forms (e.g. 啟 and 啓) are canonicals in v2r, so this needs manual review.


Finally,  reviewed and manually resolved:

啟, 眾, 缽, 說, 贗
