import pytest
from unittest.mock import MagicMock, patch
import sys

# Mock boto3 at module level before importing app_logger
sys.modules['boto3'] = MagicMock()

from config.app_logger import (
    StdoutAuditSink,
    CompositeAuditSink,
    CloudWatchAuditSink,
    S3AuditSink,
    configure_audit_sink,
    get_audit_sink,
    set_audit_sink,
    log_audit_event,
    reset_current_audit_hash,
    set_current_audit_hash,
)


def test_stdout_audit_sink_write():
    """Test StdoutAuditSink writes without error."""
    sink = StdoutAuditSink()
    event = {"event": "test", "timestamp": "2024-01-01T00:00:00Z", "data": "value"}
    sink.write(event)  # Should not raise
    sink.close()  # Should not raise


def test_composite_audit_sink():
    """Test CompositeAuditSink writes to all sinks."""
    mock_sink1 = MagicMock()
    mock_sink2 = MagicMock()
    composite = CompositeAuditSink([mock_sink1, mock_sink2])
    
    event = {"event": "test", "data": "value"}
    composite.write(event)
    
    mock_sink1.write.assert_called_once_with(event)
    mock_sink2.write.assert_called_once_with(event)
    
    composite.close()
    mock_sink1.close.assert_called_once()
    mock_sink2.close.assert_called_once()


def test_configure_audit_sink_stdout():
    """Test configuring stdout sink."""
    with patch.dict("os.environ", {"AUDIT_SINK": "stdout"}):
        configure_audit_sink()
        sink = get_audit_sink()
        assert isinstance(sink, StdoutAuditSink)


def test_configure_audit_sink_invalid():
    """Test invalid sink type raises."""
    with patch.dict("os.environ", {"AUDIT_SINK": "invalid"}):
        with pytest.raises(RuntimeError, match="Unknown AUDIT_SINK"):
            configure_audit_sink()


def test_log_audit_event_uses_sink():
    """Test log_audit_event writes to configured sink."""
    mock_sink = MagicMock()
    set_audit_sink(mock_sink)
    
    token = set_current_audit_hash("")
    try:
        log_audit_event("test_event", user="test@example.com", action="login")
        
        mock_sink.write.assert_called_once()
        call_args = mock_sink.write.call_args[0][0]
        assert call_args["event"] == "test_event"
        assert call_args["user"] == "test@example.com"
        assert call_args["action"] == "login"
        assert "audit_hash" in call_args
        assert "prev_audit_hash" in call_args
        assert "correlation_id" in call_args
    finally:
        reset_current_audit_hash(token)
        set_audit_sink(StdoutAuditSink())


def test_s3_audit_sink_requires_boto3():
    """Test S3AuditSink raises without boto3."""
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(RuntimeError, match="boto3 required"):
            S3AuditSink("bucket")


def test_cloudwatch_audit_sink_requires_boto3():
    """Test CloudWatchAuditSink raises without boto3."""
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(RuntimeError, match="boto3 required"):
            CloudWatchAuditSink("group", "stream")


def test_s3_audit_sink_buffering():
    """Test S3AuditSink buffers and flushes."""
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3
        
        sink = S3AuditSink("bucket", prefix="audit/", object_lock_days=1)
        # Write a few events
        for i in range(3):
            sink.write({"event": f"test_{i}", "data": i})
        
        # Buffer should not have flushed yet (max_batch=100)
        mock_s3.put_object.assert_not_called()
        
        # Force flush
        sink.flush()
        mock_s3.put_object.assert_called_once()
        
        # Verify put_object call has WORM settings
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "bucket"
        assert "ObjectLockMode" in call_kwargs
        assert call_kwargs["ObjectLockMode"] == "COMPLIANCE"
        assert "ObjectLockRetainUntilDate" in call_kwargs
        
        sink.close()


def test_s3_audit_sink_kms_encryption():
    """Test S3AuditSink with KMS key."""
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3
        
        sink = S3AuditSink("bucket", kms_key_id="arn:aws:kms:...:key/...")
        sink.write({"event": "test"})
        sink.flush()
        
        call_kwargs = mock_s3.put_object.call_args.kwargs
        assert call_kwargs.get("ServerSideEncryption") == "aws:kms"
        assert call_kwargs.get("SSEKMSKeyId") == "arn:aws:kms:...:key/..."


def test_composite_sink_with_multiple():
    """Test composite sink with stdout + S3 + CloudWatch."""
    with patch("boto3.client") as mock_boto:
        mock_s3 = MagicMock()
        mock_cw = MagicMock()
        mock_boto.side_effect = lambda service, **kw: mock_s3 if service == "s3" else mock_cw
        
        with patch.dict("os.environ", {
            "AUDIT_SINK": "composite",
            "AUDIT_SINKS": "stdout,s3,cloudwatch",
            "AUDIT_S3_BUCKET": "bucket",
            "AUDIT_CW_GROUP": "group",
            "AUDIT_CW_STREAM": "stream",
        }):
            configure_audit_sink()
            sink = get_audit_sink()
            
            assert isinstance(sink, CompositeAuditSink)
            assert len(sink._sinks) == 3
            assert isinstance(sink._sinks[0], StdoutAuditSink)
            assert isinstance(sink._sinks[1], S3AuditSink)
            assert isinstance(sink._sinks[2], CloudWatchAuditSink)