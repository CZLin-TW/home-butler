def handle_set_style(data, user_name, ctx):
    """設定用戶自訂管家風格"""
    style = data.get("style", "").strip()
    members = ctx.get("家庭成員")
    sheet = ctx.get_worksheet("家庭成員")
    header = sheet.row_values(1)
    try:
        col_index = header.index("管家風格") + 1
    except ValueError:
        return "❌ 找不到「管家風格」欄位，請先在 Google Sheets 家庭成員分頁新增此欄"
    for i, row in enumerate(members):
        if row.get("名稱") == user_name and row.get("狀態") == "啟用":
            sheet.update_cell(i + 2, col_index, style)
            if style:
                return f"✅ 管家風格已更新為：{style}"
            else:
                return "✅ 管家風格已恢復預設"
    return "❌ 找不到您的成員資料"
