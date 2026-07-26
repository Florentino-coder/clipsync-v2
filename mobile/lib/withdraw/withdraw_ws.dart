import 'withdraw_order.dart';
import 'withdraw_queue.dart';

/// Process-wide queue for the FGS isolate (v1 in-memory while service alive).
class WithdrawQueueStore {
  WithdrawQueueStore._();
  static final WithdrawQueue instance = WithdrawQueue();
}

bool handleWithdrawNotifyMessage(Map<String, dynamic> msg, WithdrawQueue queue) {
  if ((msg['type'] as String?) != 'withdraw_notify') return false;
  queue.upsert(WithdrawOrder.fromRelayJson(msg));
  return true;
}

bool handleSlipStatusMessage(Map<String, dynamic> msg, WithdrawQueue queue) {
  if ((msg['type'] as String?) != 'slip_status') return false;
  final orderId = (msg['order_id'] as String?)?.trim() ?? '';
  if (orderId.isEmpty) return false;
  final stage = (msg['stage'] as String?)?.trim() ?? '';
  switch (stage) {
    case 'done':
      queue.markSucceeded(orderId);
      return true;
    case 'processing':
    case 'received':
      queue.markProcessing(orderId);
      return true;
    case 'failed':
      queue.markFailed(
        orderId,
        reason: (msg['reason'] as String?) ?? '',
      );
      return true;
    default:
      return false;
  }
}
