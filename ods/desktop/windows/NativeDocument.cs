// SPDX-License-Identifier: Apache-2.0
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace OdsDesktop
{
    public sealed class NativeDocument
    {
        public const string FileName = "document.md";
        public const int MaximumBytes = 64 * 1024;
        public readonly string Content, FileSha256, SnapshotSha256, SiteId;
        readonly byte[] bytes;

        NativeDocument(string content, byte[] body)
        {
            Content = content; bytes = body;
            FileSha256 = Hash(body);
            byte[] name = Encoding.UTF8.GetBytes(FileName);
            using (var snapshot = new MemoryStream())
            {
                BigEndian(snapshot, (ulong)name.Length, 4); snapshot.Write(name, 0, name.Length);
                BigEndian(snapshot, (ulong)body.Length, 8); snapshot.Write(body, 0, body.Length);
                SnapshotSha256 = Hash(snapshot.ToArray());
            }
            SiteId = "site-" + SnapshotSha256.Substring(0, 24);
        }
        static void BigEndian(Stream output, ulong value, int count)
        { for (int index = count - 1; index >= 0; index--) output.WriteByte((byte)(value >> (index * 8))); }
        static string Hash(byte[] body)
        { using (var hash = new SHA256Cng()) return BitConverter.ToString(hash.ComputeHash(body)).Replace("-", "").ToLowerInvariant(); }

        public static NativeDocument Prepare(string markdown)
        {
            if (String.IsNullOrWhiteSpace(markdown) || markdown.Length > MaximumBytes ||
                markdown.Any(value => Char.IsControl(value) && value != '\n' && value != '\r' && value != '\t'))
                throw new ArgumentException("The document must contain Markdown text of at most 64 KiB, without unsupported control characters.");
            byte[] body;
            try { body = new UTF8Encoding(false, true).GetBytes(markdown); }
            catch (EncoderFallbackException error) { throw new ArgumentException("The document must contain valid Unicode text.", error); }
            if (body.Length > MaximumBytes) throw new ArgumentException("The UTF-8 document exceeds 64 KiB.");
            return new NativeDocument(markdown, body);
        }
        public byte[] ReadBytes() { return (byte[])bytes.Clone(); }
        public object Receipt()
        {
            return new Dictionary<string, object> {
                {"schemaVersion", 1}, {"kind", "ods-pixel-workspace-artifact"}, {"relativePath", FileName},
                {"siteId", SiteId}, {"sha256", SnapshotSha256},
                {"file", new Dictionary<string, object> { {"path", FileName}, {"bytes", bytes.Length}, {"sha256", FileSha256} }}
            };
        }
        internal bool MatchesReceipt(object value)
        {
            var receipt = value as Dictionary<string, object>;
            object fileValue;
            if (receipt == null || receipt.Count != 6 || !receipt.TryGetValue("file", out fileValue)) return false;
            var file = fileValue as Dictionary<string, object>;
            return EqualsValue(receipt, "schemaVersion", 1) && EqualsValue(receipt, "kind", "ods-pixel-workspace-artifact") &&
                EqualsValue(receipt, "relativePath", FileName) && EqualsValue(receipt, "siteId", SiteId) &&
                EqualsValue(receipt, "sha256", SnapshotSha256) && file != null && file.Count == 3 &&
                EqualsValue(file, "path", FileName) && EqualsValue(file, "bytes", bytes.Length) && EqualsValue(file, "sha256", FileSha256);
        }
        static bool EqualsValue(Dictionary<string, object> value, string key, object expected)
        { object actual; return value.TryGetValue(key, out actual) && Equals(actual, expected); }
    }
}
