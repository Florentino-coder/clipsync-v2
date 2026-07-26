import 'package:clipsync_app/withdraw/withdraw_order.dart';
import 'package:clipsync_app/withdraw/withdraw_queue.dart';
import 'package:flutter_test/flutter_test.dart';

WithdrawOrder order(String id, {int ts = 1, String amount = '10.00'}) =>
    WithdrawOrder(
      orderId: id,
      amount: amount,
      account: '4774090171',
      bank: 'KBANK',
      accountName: '',
      ts: ts,
    );

void main() {
  test('active is newest pending by ts', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1));
    q.upsert(order('B', ts: 2));
    expect(q.active?.orderId, 'B');
  });

  test('dedupe same order_id updates in place', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1, amount: '10.00'));
    q.upsert(order('A', ts: 2, amount: '11.00'));
    expect(q.pending.length, 1);
    expect(q.active?.amount, '11.00');
  });

  test('copy targets active amount and account', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1));
    q.upsert(order('B', ts: 2, amount: '99.00'));
    expect(q.copyAmountText(), '99.00');
    expect(q.copyAccountText(), '4774090171');
  });

  test('setActive changes copy target', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1, amount: '10.00'));
    q.upsert(order('B', ts: 2, amount: '20.00'));
    q.setActive('A');
    expect(q.copyAmountText(), '10.00');
  });

  test('markProcessing hides copy for that order_id', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1));
    q.markProcessing('A');
    expect(q.canCopy('A'), isFalse);
    expect(q.copyAmountText(), isNull);
  });

  test('markDone removes from queue', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1));
    q.markDone('A');
    expect(q.pending, isEmpty);
  });

  test('markFailed keeps fail state and does not requeue as pending', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1));
    q.markFailed('A', reason: 'timeout');
    expect(q.stateOf('A'), WithdrawItemState.failed);
    q.upsert(order('A', ts: 3)); // silent re-notify must not unlock copy
    expect(q.canCopy('A'), isFalse);
    expect(q.stateOf('A'), WithdrawItemState.failed);
  });

  test('clearPending removes only pending items', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1));
    q.upsert(order('B', ts: 2));
    q.markProcessing('B');
    q.clearPending();
    expect(q.pending, isEmpty);
    expect(q.stateOf('B'), WithdrawItemState.processing);
    expect(q.canCopy('A'), isFalse);
  });

  test('markSucceeded sets done and hides copy but stays visible', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1));
    q.markSucceeded('A');
    expect(q.stateOf('A'), WithdrawItemState.done);
    expect(q.canCopy('A'), isFalse);
    expect(q.pending, isEmpty);
    expect(q.visibleOrders.map((o) => o.orderId), ['A']);
  });

  test('markDone after succeeded removes from visibleOrders', () {
    final q = WithdrawQueue();
    q.upsert(order('A', ts: 1));
    q.markSucceeded('A');
    q.markDone('A');
    expect(q.visibleOrders, isEmpty);
    expect(q.stateOf('A'), isNull);
  });

  test('upsert returns false when order_id already pending', () {
    final q = WithdrawQueue();
    final o = order('acct:0618407497', ts: 1);
    expect(q.upsert(o), isTrue);
    expect(q.upsert(o.copyWith(ts: 2)), isFalse);
  });
}
