class SlipEvent {
  final String eventId;
  final String capturedAt;
  final String bank;
  final double? amount;
  final String? senderName;
  final String? receiverAccountLast4;
  final String? senderAccountLast4;
  final String? receiverAccountMasked;
  final String? senderAccountMasked;
  final String? receiverBank;
  final String? refNumber;
  final double ocrConfidence;
  final bool parseFailed;
  /// `high` | `needs_review` — position-based จาก/ไปยัง confidence.
  final String? accountParseConfidence;
  final String localImagePath;

  const SlipEvent({
    required this.eventId,
    required this.capturedAt,
    required this.bank,
    this.amount,
    this.senderName,
    this.receiverAccountLast4,
    this.senderAccountLast4,
    this.receiverAccountMasked,
    this.senderAccountMasked,
    this.receiverBank,
    this.refNumber,
    required this.ocrConfidence,
    required this.parseFailed,
    this.accountParseConfidence,
    required this.localImagePath,
  });

  Map<String, dynamic> toJson() => {
        'event_id': eventId,
        'captured_at': capturedAt,
        'bank': bank,
        'amount': amount,
        'sender_name': senderName,
        'receiver_account_last4': receiverAccountLast4,
        'sender_account_last4': senderAccountLast4,
        'receiver_account_masked': receiverAccountMasked,
        'sender_account_masked': senderAccountMasked,
        'receiver_bank': receiverBank,
        'ref_number': refNumber,
        'ocr_confidence': ocrConfidence,
        'parse_failed': parseFailed,
        if (accountParseConfidence != null)
          'account_parse_confidence': accountParseConfidence,
      };
}
