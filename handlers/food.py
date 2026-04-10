from config import now_taipei
from sheets import build_row


def handle_add(data, user_name, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    today = now_taipei().strftime("%Y-%m-%d")
    headers = sheet.row_values(1)
    sheet.append_row(build_row(headers, {
        "品名": data.get("name", ""),
        "數量": data.get("quantity", 1),
        "單位": data.get("unit", ""),
        "過期日": data.get("expiry", ""),
        "新增日": today,
        "新增者": user_name,
        "狀態": "有效",
    }))
    return f"✅ 已新增 {data.get('name')}，過期日 {data.get('expiry')}"


def handle_delete(data, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    archive = ctx.get_worksheet("食品封存")
    records = ctx.get("食品庫存")
    archive_headers = archive.row_values(1)
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            archive.append_row(build_row(archive_headers, {**row, "狀態": "已消耗"}))
            sheet.delete_rows(i + 2)
            records.pop(i)
            return f"✅ 已標記 {data.get('name')} 為已消耗"
    return f"❌ 找不到 {data.get('name')}"


def handle_modify(data, ctx):
    sheet = ctx.get_worksheet("食品庫存")
    archive = ctx.get_worksheet("食品封存")
    records = ctx.get("食品庫存")
    archive_headers = archive.row_values(1)
    for i, row in enumerate(records):
        if row.get("品名") == data.get("name") and row.get("狀態") == "有效":
            if data.get("quantity") is not None and int(data.get("quantity", 1)) <= 0:
                archive.append_row(build_row(archive_headers, {**row, "狀態": "已消耗"}))
                sheet.delete_rows(i + 2)
                records.pop(i)
                return f"✅ {data.get('name')} 已全部消耗"
            col = {h: idx + 1 for idx, h in enumerate(sheet.row_values(1))}
            if data.get("name_new"):
                sheet.update_cell(i + 2, col["品名"], data.get("name_new"))
            if data.get("quantity") is not None:
                sheet.update_cell(i + 2, col["數量"], int(data.get("quantity")))
            if data.get("unit"):
                sheet.update_cell(i + 2, col["單位"], data.get("unit"))
            if data.get("expiry"):
                sheet.update_cell(i + 2, col["過期日"], data.get("expiry"))
            return f"✅ {data.get('name')} 已更新"
    return f"❌ 找不到 {data.get('name')}"


def handle_query(ctx):
    valid = [r for r in ctx.get("食品庫存") if r.get("狀態") == "有效"]
    if not valid:
        return "目前庫存是空的"
    lines = [f"• {r['品名']} {r['數量']}{r['單位']}（{r['過期日']}）" for r in valid]
    return "目前庫存：\n" + "\n".join(lines)
