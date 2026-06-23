# Procedure

`/home/chris/Dropbox/projects/bkk/tools/survey-out/appendix1_variants.tsv` holds the variants defined in 通用规范汉字表, published 2013 in the PR China. We will use this to seed the canonicalization list for BKK as follows: Characters in the 'traditional' column become part of the character set.  Characters in the 'regulated' column will be replaced with the traditional version. In addition, the characters listed as variants will also be replaced by the traditional variant.

Additional variant mappings comes from `/home/chris/Dropbox/projects/bkk/tools/survey-out/Chinese Var-to-Rep_v1_0.tsv`. Please merge this with the above list to form 'bkk-variant-pairs.tsv' with the following format:

var_cp	var_char	reg_cp	reg_char	remarks

Characters should not occur in the var and reg column.  Such cases should be flagged in remarks.

The result is in 
`v2r-ge065.tsv`
now also augmented with pairs from twjp-vardb
