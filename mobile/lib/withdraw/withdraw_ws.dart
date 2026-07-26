import 'withdraw_order.dart';
import 'withdraw_queue.dart';

/// Process-wide queue for the FGS isolate (v1 in-memory while service alive).
class WithdrawQueueStore {
  WithdrawQueueStore._();
  static final WithdrawQueue instance = WithdrawQueue();
}

class WithdrawNotifyHandleResult {
  const WithdrawNotifyHandleResult({required this.ok, required this.isNew});
  final bool ok;
  final bool isNew;
}

WithdrawNotifyHandleResult handleWithdrawNotifyMessage(
  Map<String, dynamic> msg,
  WithdrawQueue queue,
) {
  if ((msg['type'] as String?) != 'withdraw_notify') {
    return const WithdrawNotifyHandleResult(ok: false, isNew: false);
  }
  final isNew = queue.upsert(WithdrawOrder.fromRelayJson(msg));
  return WithdrawNotifyHandleResult(ok: true, isNew: isNew);
}

/// Apply slip_status to [queue]. Returns resolved order_id when applied, else null.
String? handleSlipStatusMessage(Map<String, dynamic> msg, WithdrawQueue queue) {
  if ((msg['type'] as String?) != 'slip_status') return null;
  var orderId = (msg['order_id'] as String?)?.trim() ?? '';
  // PC may emit amount push_id (1900.00) when scrape order_id was missing —
  // resolve to the pending acct:… row by amount.
  if (orderId.isEmpty || queue.stateOf(orderId) == null) {
    final amount = (msg['amount'] as String?)?.trim() ?? '';
    final amountKey =
        amount.isNotEmpty ? amount : (orderId.isNotEmpty ? orderId : '');
    final resolved = queue.findOrderIdByAmount(amountKey);
    if (resolved != null) {
      orderId = resolved;
    }
  }
  if (orderId.isEmpty) return null;
  final stage = (msg['stage'] as String?)?.trim() ?? '';
  switch (stage) {
    case 'done':
      queue.markSucceeded(orderId);
      return orderId;
    case 'processing':
    case 'received':
      queue.markProcessing(orderId);
      return orderId;
    case 'failed':
      queue.markFailed(
        orderId,
        reason: (msg['reason'] as String?) ?? '',
      );
      return orderId;
    default:
      return null;
  }
}
