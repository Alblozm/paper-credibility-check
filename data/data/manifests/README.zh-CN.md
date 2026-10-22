# 论文清单与数据集版本

## papers.csv

这是论文主清单。每篇候选论文占一行，记录：

- 内部`paper_id`；
- arXiv和OpenReview标识符；
- venue、年份和公开decision；
- 样本层级与评估难点标签；
- 选择原因和当前处理状态。

Accept或Reject只用于样本选择和后续比较，不能直接作为五维评分真值。

## dataset.yaml

记录数据集名称、版本、Rubric版本、目标论文数量以及calibration/test划分。

已发布的数据集版本不得静默覆盖。修正正式数据时，应升级`dataset_version`并记录修改原因。
