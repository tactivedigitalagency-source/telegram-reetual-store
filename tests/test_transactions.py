import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db

ADDRESS={"name":"Tester","phone":"08123456789","address":"Jalan Contoh 10 RT 01","subdistrict":"Wonokromo","city":"Surabaya","province":"Jawa Timur","postal":"60243"}

class TransactionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        os.environ['DATABASE_PATH']=self.tmp.name+'/store.sqlite3'
        db.init()
        db.ensure_user(100)
        db.ensure_user(200)
        self.pid=db.add_product('TEST','Produk uji',100000,1,'Bukan barang nyata')
    def tearDown(self):
        self.tmp.cleanup()

    def test_order_free_shipping_reserves_once(self):
        db.cart_add(100,self.pid,1)
        a=db.create_order(100,ADDRESS,'key1',999)
        b=db.create_order(100,ADDRESS,'key1',999)
        self.assertEqual(a['id'],b['id'])
        self.assertEqual(a['shipping_fee_rp'],0)
        self.assertEqual(a['total_rp'],100000)
        p=db.get_product(self.pid)
        self.assertEqual(p['reserved_qty'],1)
        self.assertEqual(p['stock_qty'],1)
        self.assertEqual(len(db.get_cart(100)),0)

    def test_last_item_concurrent(self):
        db.cart_add(100,self.pid,1)
        db.cart_add(200,self.pid,1)
        barrier=threading.Barrier(2)
        results=[]
        lock=threading.Lock()
        def task(user):
            barrier.wait()
            try:
                o=db.create_order(user,ADDRESS,f'key{user}',999)
                outcome=('success',o['id'])
            except ValueError:
                outcome=('out',user)
            with lock:
                results.append(outcome)
        t1=threading.Thread(target=task,args=(100,))
        t2=threading.Thread(target=task,args=(200,))
        t1.start();t2.start();t1.join();t2.join()
        self.assertEqual([r[0] for r in results].count('success'),1)
        self.assertEqual(db.get_product(self.pid)['reserved_qty'],1)

    def test_payment_verified_not_twice(self):
        db.cart_add(100,self.pid,1)
        o=db.create_order(100,ADDRESS,'kpaid',999)
        db.submit_proof(o['id'],100,'telegram-file-id',999)
        db.verify_payment(o['id'],999)
        with self.assertRaises(ValueError):
            db.verify_payment(o['id'],999)
        self.assertEqual(db.get_order(o['id'])['status'],'PAID')
        self.assertEqual(db.get_product(self.pid)['stock_qty'],0)
        self.assertEqual(db.get_product(self.pid)['reserved_qty'],0)

    def test_expiry_releases_only_once(self):
        db.cart_add(100,self.pid,1)
        o=db.create_order(100,ADDRESS,'kexpire',999)
        with db.connect() as c:
            c.execute('UPDATE orders SET payment_deadline=? WHERE id=?', ((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(timespec='seconds'),o['id']))
        self.assertEqual(db.expire_orders(),1)
        self.assertEqual(db.expire_orders(),0)
        self.assertEqual(db.get_product(self.pid)['reserved_qty'],0)
        self.assertEqual(db.get_order(o['id'])['status'],'EXPIRED')

    def test_proof_blocks_expiry(self):
        db.cart_add(100,self.pid,1)
        o=db.create_order(100,ADDRESS,'kreview',999)
        db.submit_proof(o['id'],100,'proof',999)
        with db.connect() as c:
            c.execute('UPDATE orders SET payment_deadline=? WHERE id=?', ((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(timespec='seconds'),o['id']))
        self.assertEqual(db.expire_orders(),0)
        self.assertEqual(db.get_order(o['id'])['status'],'PAYMENT_REVIEW')

    def test_refund_manual(self):
        db.cart_add(100,self.pid,1)
        o=db.create_order(100,ADDRESS,'krefund',999)
        db.submit_proof(o['id'],100,'proof',999)
        db.verify_payment(o['id'],999)
        db.mark_issue(o['id'],999,'Supplier tidak punya stok')
        db.request_refund(o['id'],999,100000,'Pengembalian dana penuh')
        self.assertEqual(db.get_order(o['id'])['status'],'REFUND_PENDING')
        db.complete_refund(o['id'],999,'BANKREF2026')
        self.assertEqual(db.get_order(o['id'])['status'],'REFUNDED')
        self.assertEqual(db.get_product(self.pid)['stock_qty'],0) # no automatic fictional restock

    def test_stock_cannot_go_below_reserved(self):
        db.cart_add(100,self.pid,1)
        db.create_order(100,ADDRESS,'kstock',999)
        with self.assertRaises(ValueError):
            db.update_product_stock(self.pid,0,999)

    def test_order_access_scoped(self):
        db.cart_add(100,self.pid,1)
        o=db.create_order(100,ADDRESS,'kaccess',999)
        self.assertIsNone(db.get_order(o['id'],200))

if __name__ == '__main__': unittest.main()