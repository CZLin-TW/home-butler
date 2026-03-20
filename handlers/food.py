from config import now_taipei


def handle_add(data, user_name, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    today = now_taipei().strftime("%Y-%m-%d")
    sheet.append_row([
        data.get("name", ""),
        data.get("quantity", 1),
        data.get("unit", ""),
        data.get("expiry", ""),
        today,
        user_name,
        "有效"
    ])
    return f"✅ 已新增 {data.get('name')}，過期日 {data.get('expiry')}"


def handle_delete(data, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    archive = ctx.get_worksheet("食品封存")
    records = ctx.get("食品庫存")
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            archive.append_row([
                row.get("品名"), row.get("數量"), row.get("單位"),
                row.get("過期日"), row.get("新增日"), row.get("新增者"), "已消耗"
            ])
            sheet.delete_rows(i + 2)
            records.pop(i)
            return f"✅ 已標記 {data.get('name')} 為已消耗"
    return f"❌ 找不到 {data.get('name')}"


def handle_modify(data, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    archive = ctx.get_worksheet("食品封存")
    records = ctx.get("食品庫存")
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            if data.get("quantity") is not None and int(data.get("quantity", 1)) <= 0:
                archive.append_row([
                    row.get("品名"), row.get("數量"), row.get("單位"),
                    row.get("過期日"), row.get("新增日"), row.get("新增者"), "已消耗"
                ])
                sheet.delete_rows(i + 2)
                records.pop(i)
                return f"✅ {data.get('name')} 已全部消耗"
            if data.get("name_new"):
                sheet.update_cell(i + 2, 1, data.get("name_new"))
            if data.get("quantity") is not None:
                sheet.update_cell(i + 2, 2, int(data.get("quantity")))
            if data.get("unit"):
                sheet.update_cell(i + 2, 3, data.get("unit"))
            if data.get("expiry"):
                sheet.update_cell(i + 2, 4, data.get("expiry"))
            return f"✅ {data.get('name')} 已更新"
    return f"❌ 找不到 {data.get('name')}"


def handle_query(ctx):
    valid = [r for r in ctx.get("食品庫存") if r.get("狀態") == "有效"]
    if not valid:
        return "目前庫存是空的"
    lines = [f"• {r['品名']} {r['數量']}{r['單位']}（{r['過期日']}）" for r in valid]
    return "目前庫存：\n" + "\n".join(lines)
