"""ERP/表格导出 → products.json 迁移模板（P2-7）。

真实店铺的商品数据一般在 ERP、进销存或一张 Excel 里。把它另存为 CSV（UTF-8），
用本脚本一条命令转成 agent 可用的 prompts/products.json，转换过程复用
core.knowledge.validate_products 做 schema 校验——坏行当场报出来，不带病上线。

    python3 examples/products_from_csv.py 商品导出.csv                    # 预览+校验,不写文件
    python3 examples/products_from_csv.py 商品导出.csv -o prompts/products.json  # 确认后写入

CSV 列名(表头,顺序随意;*为必填):
    product_id* , name* , price* , category , spec , stock , promotion , status , keywords
    - status: on_sale | off_sale | out_of_stock(留空=on_sale)
    - keywords: 用竖线分隔,如 "坚果|礼盒|老客价"(客户口语里的叫法都放进来,召回全靠它)
    - 列名兼容常见中文表头:编码/商品编码→product_id,名称/商品名称→name,价格/售价→price 等
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge import validate_products

# 常见中文表头 → 标准字段(按你的 ERP 导出格式随意扩充)
_HEADER_ALIASES = {
    "编码": "product_id", "商品编码": "product_id", "货号": "product_id", "sku": "product_id",
    "名称": "name", "商品名称": "name", "品名": "name",
    "价格": "price", "售价": "price", "单价": "price", "零售价": "price",
    "分类": "category", "类目": "category",
    "规格": "spec", "库存": "stock", "库存数": "stock",
    "促销": "promotion", "活动": "promotion",
    "状态": "status", "关键词": "keywords", "别名": "keywords",
}
_STATUS_ALIASES = {"在售": "on_sale", "下架": "off_sale", "已下架": "off_sale",
                   "售罄": "out_of_stock", "已售罄": "out_of_stock", "缺货": "out_of_stock"}


def convert_row(row: dict) -> dict:
    """一行 CSV → 一条商品 dict(空值剔除;价格/库存转数值;keywords 竖线切分)。"""
    item: dict = {}
    for raw_key, raw_val in row.items():
        key = _HEADER_ALIASES.get((raw_key or "").strip().lower(),
                                  _HEADER_ALIASES.get((raw_key or "").strip(), (raw_key or "").strip()))
        val = (raw_val or "").strip()
        if not val:
            continue
        if key == "price":
            try:
                item[key] = float(val) if "." in val else int(val)
            except ValueError:
                item[key] = val  # 留给 schema 校验去报,报文里带行号
        elif key == "stock":
            try:
                item[key] = int(val)
            except ValueError:
                item[key] = val
        elif key == "keywords":
            item[key] = [k.strip() for k in val.replace("｜", "|").split("|") if k.strip()]
        elif key == "status":
            item[key] = _STATUS_ALIASES.get(val, val)
        else:
            item[key] = val
    return item


def main() -> int:
    parser = argparse.ArgumentParser(description="CSV → products.json 迁移(带 schema 校验)")
    parser.add_argument("csv_path", help="ERP/表格导出的 CSV 文件(UTF-8)")
    parser.add_argument("-o", "--output", default="", help="写入路径(如 prompts/products.json);不给则只预览+校验")
    args = parser.parse_args()

    try:
        with open(args.csv_path, encoding="utf-8-sig") as f:  # utf-8-sig 兼容 Excel 导出的 BOM
            products = [convert_row(r) for r in csv.DictReader(f)]
    except OSError as exc:
        print(f"读不到 CSV: {exc}", file=sys.stderr)
        return 2

    valid, problems = validate_products(products)
    print(f"共 {len(products)} 行 → 有效 {len(valid)} 条 / 问题 {len(problems)} 条")
    for p in problems:
        print(f"  ✗ {p}")

    if not args.output:
        print("\n(预览模式,未写文件;确认无误后加 -o prompts/products.json 写入)")
        for item in valid[:3]:
            print(json.dumps(item, ensure_ascii=False))
        return 1 if problems else 0

    if problems:
        print("\n存在问题行,已只写入有效条目——请回 ERP 修数据后重新导出", file=sys.stderr)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(valid, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n已写入 {args.output}({len(valid)} 条);重启 agent 生效")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
