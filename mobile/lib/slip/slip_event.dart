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
  final String? refNumber;
  final double ocrConfidence;
  final bool parseFailed;
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
    this.refNumber,
    required this.ocrConfidence,
    required this.parseFailed,
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
        'ref_number': refNumber,
        'ocr_confidence': ocrConfidence,
        'parse_failed': parseFailed,
      };
}
