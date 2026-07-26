import 'package:clipsync_app/withdraw/withdraw_inbox_page.dart';
import 'package:clipsync_app/withdraw/withdraw_order.dart';
import 'package:clipsync_app/withdraw/withdraw_queue.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('inbox lists pending order', (tester) async {
    final q = WithdrawQueue();
    q.upsert(WithdrawOrder(
      orderId: 'W-1',
      amount: '100.00',
      account: '4774090171',
      bank: 'KBANK',
      accountName: 'สมชาย',
      ts: 1,
    ));
    await tester.pumpWidget(MaterialApp(home: WithdrawInboxPage(queue: q)));
    expect(find.textContaining('100.00'), findsWidgets);
    expect(find.textContaining('4774090171'), findsWidgets);
  });

  testWidgets('inbox empty state shows Thai copy', (tester) async {
    final q = WithdrawQueue();
    await tester.pumpWidget(MaterialApp(home: WithdrawInboxPage(queue: q)));
    expect(find.text('ไม่มีรายการถอนรอโอน'), findsOneWidget);
  });

  testWidgets('inbox shows labeled copy TextButtons', (tester) async {
    final q = WithdrawQueue();
    q.upsert(WithdrawOrder(
      orderId: 'W-1',
      amount: '100.00',
      account: '4774090171',
      bank: 'KBANK',
      accountName: 'สมชาย',
      ts: 1,
    ));
    await tester.pumpWidget(MaterialApp(home: WithdrawInboxPage(queue: q)));
    expect(find.text('คัดลอกยอด'), findsOneWidget);
    expect(find.text('คัดลอกบัญชี'), findsOneWidget);
    expect(find.byType(TextButton), findsWidgets);
    expect(find.textContaining('🏦 เลขบัญชี: 4774090171'), findsOneWidget);
    expect(find.textContaining('🏧 ธนาคาร: KBANK'), findsOneWidget);
    expect(find.textContaining('👤 ชื่อบัญชี: สมชาย'), findsOneWidget);
    expect(find.text('รอโอน'), findsOneWidget);
    expect(find.text('เคลียร์งาน'), findsOneWidget);
    expect(find.byIcon(Icons.payments_outlined), findsNothing);
    expect(find.byIcon(Icons.account_balance_wallet_outlined), findsNothing);
  });

  testWidgets('tap คัดลอกยอด copies amount via callback', (tester) async {
    final q = WithdrawQueue();
    q.upsert(WithdrawOrder(
      orderId: 'W-1',
      amount: '100.00',
      account: '4774090171',
      bank: 'KBANK',
      accountName: 'สมชาย',
      ts: 1,
    ));
    String? copiedLabel;
    String? copiedText;
    await tester.pumpWidget(MaterialApp(
      home: WithdrawInboxPage(
        queue: q,
        onCopied: (label, text) {
          copiedLabel = label;
          copiedText = text;
        },
      ),
    ));
    final amountBtn = find.widgetWithText(TextButton, 'คัดลอกยอด');
    await tester.ensureVisible(amountBtn);
    expect(tester.widget<TextButton>(amountBtn).onPressed, isNotNull);
    await tester.tap(amountBtn);
    await tester.pumpAndSettle();
    expect(copiedText, '100.00');
    expect(copiedLabel, 'คัดลอกยอดแล้ว');
  });

  testWidgets('tap คัดลอกบัญชี copies account via callback', (tester) async {
    final q = WithdrawQueue();
    q.upsert(WithdrawOrder(
      orderId: 'W-1',
      amount: '100.00',
      account: '4774090171',
      bank: 'KBANK',
      accountName: 'สมชาย',
      ts: 1,
    ));
    String? copiedLabel;
    String? copiedText;
    await tester.pumpWidget(MaterialApp(
      home: WithdrawInboxPage(
        queue: q,
        onCopied: (label, text) {
          copiedLabel = label;
          copiedText = text;
        },
      ),
    ));
    final accountBtn = find.widgetWithText(TextButton, 'คัดลอกบัญชี');
    await tester.ensureVisible(accountBtn);
    expect(tester.widget<TextButton>(accountBtn).onPressed, isNotNull);
    await tester.tap(accountBtn);
    await tester.pumpAndSettle();
    expect(copiedText, '4774090171');
    expect(copiedLabel, 'คัดลอกบัญชีแล้ว');
  });

  testWidgets('เคลียร์งาน confirms then clears pending queue', (tester) async {
    final q = WithdrawQueue();
    q.upsert(WithdrawOrder(
      orderId: 'W-1',
      amount: '100.00',
      account: '4774090171',
      bank: 'KBANK',
      accountName: 'สมชาย',
      ts: 1,
    ));
    var cleared = false;
    await tester.pumpWidget(MaterialApp(
      home: WithdrawInboxPage(
        queue: q,
        onCleared: () async {
          cleared = true;
        },
      ),
    ));
    expect(find.text('เคลียร์งาน'), findsOneWidget);
    await tester.tap(find.text('เคลียร์งาน'));
    await tester.pumpAndSettle();
    expect(find.text('ล้างรายการถอนรอโอนทั้งหมด?'), findsOneWidget);
    await tester.tap(find.text('เคลียร์'));
    await tester.pumpAndSettle();
    expect(q.pending, isEmpty);
    expect(cleared, isTrue);
    expect(find.text('ไม่มีรายการถอนรอโอน'), findsOneWidget);
  });

  testWidgets('done order shows สำเร็จ in inbox', (tester) async {
    final q = WithdrawQueue();
    q.upsert(WithdrawOrder(
      orderId: 'A',
      amount: '100.00',
      account: '4774090171',
      bank: 'KBANK',
      accountName: '',
      ts: 1,
    ));
    q.markSucceeded('A');

    await tester.pumpWidget(
      MaterialApp(home: WithdrawInboxPage(queue: q)),
    );
    expect(find.textContaining('สำเร็จ'), findsWidgets);
  });
}
