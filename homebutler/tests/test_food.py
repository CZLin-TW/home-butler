import unittest
from datetime import date

from homebutler.domain import food, households
from homebutler.store.models import FoodItem, Member
from homebutler.tests.helpers import StoreTest

D = date.fromisoformat
TODAY = D("2026-10-21")


class FoodTests(StoreTest):
    def setUp(self):
        super().setUp()
        with self.store.transaction() as s:
            self.home = households.ensure(s, "A").id
            self.other = households.ensure(s, "B").id
            s.add(Member(household_id=self.home, name="爸", line_user_id="U1", active=True))

    def me(self, s):
        return s.query(Member).one()

    def test_add_list_and_bad_input(self):
        with self.store.transaction() as s:
            self.assertEqual(food.add(s, self.me(s), TODAY, name="牛奶", quantity=2, unit="瓶", expiry="2026-10-25").message,
                             "✅ 已新增 牛奶，過期日 2026-10-25")
            self.assertEqual(food.add(s, self.me(s), TODAY, name="米").message, "✅ 已新增 米，過期日 未設定")
            self.assertFalse(food.add(s, self.me(s), TODAY, name="").ok)
            self.assertFalse(food.add(s, self.me(s), TODAY, name="蛋", quantity=0).ok)
            self.assertFalse(food.add(s, self.me(s), TODAY, name="蛋", expiry="明天").ok)
            self.assertEqual(food.listing(food.active(s)), "目前庫存：\n• 牛奶 2瓶（2026-10-25）\n• 米 1個（未設定）")

    def test_same_name_uses_the_one_expiring_first_and_says_which(self):
        with self.store.transaction() as s:
            food.add(s, self.me(s), TODAY, name="牛奶", expiry="2026-10-30")
            food.add(s, self.me(s), TODAY, name="牛奶", expiry="2026-10-23")
            self.assertEqual(food.consume(s, self.home, name="牛奶").message, "✅ 已標記 牛奶（2026-10-23） 為已消耗")
            self.assertEqual(food.consume(s, self.home, name="牛奶").message, "✅ 已標記 牛奶 為已消耗")
            self.assertEqual(food.consume(s, self.home, name="牛奶").message, "❌ 找不到 牛奶")
            self.assertEqual(s.query(FoodItem).filter_by(status="consumed").count(), 2)   # kept, not deleted

    def test_modify_and_zero_means_used_up(self):
        with self.store.transaction() as s:
            food.add(s, self.me(s), TODAY, name="蛋", quantity=10)
            item = s.query(FoodItem).one()
            self.assertEqual(food.modify(s, self.home, {"quantity": "6", "name": "雞蛋"}, name="蛋").message, "✅ 蛋 已更新")
            self.assertEqual((item.name, item.quantity), ("雞蛋", 6))
            self.assertIn("沒收到任何要更新的欄位", food.modify(s, self.home, {}, item_id=item.id).message)
            self.assertEqual(food.modify(s, self.home, {"quantity": 0}, item_id=item.id).message, "✅ 雞蛋 已全部消耗")
            self.assertEqual(food.active(s), [])

    def test_households_are_kept_apart_and_expiring_list(self):
        with self.store.transaction() as s:
            food.add(s, None, TODAY, name="豆腐", expiry="2026-10-22", household_id=self.other)
            food.add(s, self.me(s), TODAY, name="優格", expiry="2026-10-20")
            food.add(s, self.me(s), TODAY, name="起司", expiry="2026-10-24")
            self.assertEqual(food.consume(s, self.home, name="豆腐").message, "❌ 找不到 豆腐")
            self.assertEqual([i.name for i in food.expiring(s, TODAY, self.home)], ["優格"])
            self.assertEqual([i.name for i in food.expiring(s, TODAY)], ["優格", "豆腐"])


if __name__ == "__main__":
    unittest.main()
