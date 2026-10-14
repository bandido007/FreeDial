"""REST serializers for the file endpoints."""

from rest_framework import serializers


class UploadFileSerializer(serializers.Serializer):
    """An upload, either as a multipart ``file`` or as JSON with ``base64_string``."""

    # 'Image' (default) or 'Document'. Kept lenient so the client can omit it.
    file_type = serializers.CharField(required=False, allow_blank=True, default="Image")
    file = serializers.FileField(required=False)
    base64_string = serializers.CharField(required=False, allow_blank=True)
    file_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    purpose = serializers.CharField(max_length=60, required=False, allow_blank=True, default="general")
    definition_uid = serializers.UUIDField(required=False, allow_null=True)
    entity_type = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    entity_uid = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs.get("file") and not (attrs.get("base64_string") or "").strip():
            raise serializers.ValidationError({"file": "Please choose a file to upload."})
        if not attrs.get("file") and not (attrs.get("file_name") or "").strip():
            raise serializers.ValidationError({"file_name": "Please choose a file to upload."})
        return attrs


class FileReferenceSerializer(serializers.Serializer):
    """Names a stored file by ID (preferred) or by its media path."""

    uid = serializers.UUIDField(required=False, allow_null=True)
    file_path = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("uid") and not (attrs.get("file_path") or "").strip():
            raise serializers.ValidationError({"file_path": "No file was specified."})
        return attrs
