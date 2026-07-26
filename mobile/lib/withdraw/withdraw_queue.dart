import 'withdraw_order.dart';

enum WithdrawItemState { pending, processing, done, failed }

class WithdrawQueue {
  final Map<String, WithdrawOrder> _orders = {};
  final Map<String, WithdrawItemState> _states = {};
  final Map<String, String> _failReasons = {};
  String? _activeOverride;

  List<WithdrawOrder> get pending {
    final list = _orders.values
        .where((o) => _states[o.orderId] == WithdrawItemState.pending)
        .toList();
    list.sort((a, b) => b.ts.compareTo(a.ts));
    return list;
  }

  /// Pending + processing + done + failed (newest first) for inbox display.
  List<WithdrawOrder> get visibleOrders {
    final list = _orders.values.toList();
    list.sort((a, b) => b.ts.compareTo(a.ts));
    return list;
  }

  WithdrawOrder? get active {
    final overrideId = _activeOverride;
    if (overrideId != null) {
      final o = _orders[overrideId];
      if (o != null && _states[overrideId] == WithdrawItemState.pending) {
        return o;
      }
    }
    final p = pending;
    if (p.isEmpty) return null;
    return p.first;
  }

  bool containsPending(String orderId) =>
      _states[orderId] == WithdrawItemState.pending;

  /// Returns true if this order_id was not already pending (new for heads-up).
  bool upsert(WithdrawOrder order) {
    final existing = _states[order.orderId];
    final wasNewPending = existing != WithdrawItemState.pending;
    _orders[order.orderId] = order;
    if (existing == WithdrawItemState.failed ||
        existing == WithdrawItemState.processing ||
        existing == WithdrawItemState.done) {
      // Keep safety state — silent re-notify must not unlock copy / heads-up.
      return false;
    }
    _states[order.orderId] = WithdrawItemState.pending;
    return wasNewPending;
  }

  void setActive(String orderId) {
    _activeOverride = orderId;
  }

  bool canCopy(String orderId) =>
      _states[orderId] == WithdrawItemState.pending;

  String? copyAmountText() {
    final o = active;
    if (o == null || !canCopy(o.orderId)) return null;
    return o.amount;
  }

  String? copyAccountText() {
    final o = active;
    if (o == null || !canCopy(o.orderId)) return null;
    return o.account;
  }

  void markProcessing(String orderId) {
    if (!_orders.containsKey(orderId)) return;
    _states[orderId] = WithdrawItemState.processing;
    if (_activeOverride == orderId) _activeOverride = null;
  }

  void markSucceeded(String orderId) {
    if (!_orders.containsKey(orderId)) return;
    _states[orderId] = WithdrawItemState.done;
    if (_activeOverride == orderId) _activeOverride = null;
  }

  void markDone(String orderId) {
    _orders.remove(orderId);
    _states.remove(orderId);
    _failReasons.remove(orderId);
    if (_activeOverride == orderId) _activeOverride = null;
  }

  void markFailed(String orderId, {String reason = ''}) {
    if (!_orders.containsKey(orderId)) return;
    _states[orderId] = WithdrawItemState.failed;
    _failReasons[orderId] = reason;
    if (_activeOverride == orderId) _activeOverride = null;
  }

  WithdrawItemState? stateOf(String orderId) => _states[orderId];

  void clearPending() {
    final ids = _orders.keys
        .where((id) => _states[id] == WithdrawItemState.pending)
        .toList(growable: false);
    for (final id in ids) {
      markDone(id);
    }
  }
}
