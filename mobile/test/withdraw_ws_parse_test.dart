import 'package:clipsync_app/withdraw/withdraw_order.dart';
import 'package:clipsync_app/withdraw/withdraw_queue.dart';
import 'package:clipsync_app/withdraw/withdraw_ws.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('handleWithdrawNotifyMessage upserts into queue', () {
    final q = WithdrawQueue();
    final ok = handleWithdrawNotifyMessage({
      'type': 'withdraw_notify',
      'order_id': 'W-1',
      'amount': '100.00',
      'account': '4774090171',
      'bank': 'KBANK',
      'account_name': '',
      'ts': 1,
    }, q);
    expect(ok, isTrue);
    expect(q.active?.orderId, 'W-1');
  });

  test('ignores clip messages', () {
    final q = WithdrawQueue();
    expect(handleWithdrawNotifyMessage({'type': 'clip', 'text': 'hi'}, q), isFalse);
    expect(q.pending, isEmpty);
  });

  test('handleSlipStatusMessage done marks succeeded', () {
    final q = WithdrawQueue();
    q.upsert(WithdrawOrder(
      orderId: 'X',
      amount: '10.00',
      account: '4774090171',
      bank: 'KBANK',
      accountName: '',
      ts: 1,
    ));
    final ok = handleSlipStatusMessage({
      'type': 'slip_status',
      'job_id': 'j',
      'order_id': 'X',
      'stage': 'done',
      'message_th': 'สำเร็จ',
      'ts': 3,
    }, q);
    expect(ok, isTrue);
    expect(q.stateOf('X'), WithdrawItemState.done);
    expect(q.canCopy('X'), isFalse);
  });

  test('handleSlipStatusMessage ignores wrong type', () {
    final q = WithdrawQueue();
    expect(handleSlipStatusMessage({'type': 'clip'}, q), isFalse);
  });
}
