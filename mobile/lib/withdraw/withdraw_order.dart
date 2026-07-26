class WithdrawOrder {
  const WithdrawOrder({
    required this.orderId,
    required this.amount,
    required this.account,
    required this.bank,
    required this.accountName,
    required this.ts,
    this.approvedAt = '',
  });

  final String orderId;
  final String amount;
  final String account;
  final String bank;
  final String accountName;
  final int ts;
  final String approvedAt;

  WithdrawOrder copyWith({
    String? orderId,
    String? amount,
    String? account,
    String? bank,
    String? accountName,
    int? ts,
    String? approvedAt,
  }) {
    return WithdrawOrder(
      orderId: orderId ?? this.orderId,
      amount: amount ?? this.amount,
      account: account ?? this.account,
      bank: bank ?? this.bank,
      accountName: accountName ?? this.accountName,
      ts: ts ?? this.ts,
      approvedAt: approvedAt ?? this.approvedAt,
    );
  }

  factory WithdrawOrder.fromRelayJson(Map<String, dynamic> json) {
    final orderId = (json['order_id'] as String?)?.trim() ?? '';
    if (orderId.isEmpty) {
      throw const FormatException('order_id is required');
    }
    return WithdrawOrder(
      orderId: orderId,
      amount: (json['amount'] as String?) ?? '',
      account: (json['account'] as String?) ?? '',
      bank: (json['bank'] as String?) ?? '',
      accountName: (json['account_name'] as String?) ?? '',
      ts: (json['ts'] as num?)?.toInt() ?? 0,
      approvedAt: (json['approved_at'] as String?)?.trim() ?? '',
    );
  }
}
