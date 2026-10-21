# Персонализация: базовый скоринг vs LLM-реранк

```
dev_loan     P@3=1.00 nDCG@5=0.69  top3=[1, 1, 2]
importer_cn  P@3=1.00 nDCG@5=0.94  top3=[2, 2, 1]
it_zpp       P@3=0.67 nDCG@5=0.71  top3=[0, 2, 2]
restaurant   P@3=0.67 nDCG@5=0.65  top3=[0, 1, 2]
landlord     P@3=0.67 nDCG@5=0.91  top3=[2, 2, 0]
trucker      P@3=0.67 nDCG@5=0.66  top3=[2, 1, 0]
СРЕДНЕЕ: P@3=0.778  nDCG@5=0.761
С LLM-реранком (6/6 профилей): P@3=0.889  nDCG@5=0.914
```
