import { Injectable, inject } from "@angular/core";
import { HttpClient, HttpParams } from "@angular/common/http";
import { firstValueFrom } from "rxjs";
import { Apollo } from "apollo-angular";
import { gql } from "@apollo/client/core";
@Injectable({ providedIn: "root" })
export class DataService {
  private http = inject(HttpClient);
  private apollo = inject(Apollo);
  get<T>(path: string, params: Record<string, string | number | boolean> = {}) {
    return firstValueFrom(this.http.get<T>("/api/workspace/" + path, { params }));
  }
  post<T>(path: string, body: any) {
    return firstValueFrom(this.http.post<T>("/api/workspace/" + path, body));
  }
  delete<T>(path: string, body: any) {
    return firstValueFrom(this.http.delete<T>("/api/workspace/" + path, { body }));
  }
  async graph<T = any>(document: string, variables: Record<string, any> = {}, mutation = false): Promise<T> {
    const options = { variables, fetchPolicy: "no-cache" as const };
    const reply: any = await firstValueFrom(
      mutation
        ? this.apollo.mutate({ mutation: gql(document), ...options })
        : this.apollo.query({ query: gql(document), ...options }),
    );
    if (reply.errors?.length) throw new Error(reply.errors[0].message);
    const answer: any = Object.values(reply.data || {})[0];
    if (!answer?.status) throw new Error(answer?.message || "Request failed. Please try again.");
    return answer.data ?? answer.dataList ?? answer;
  }
  async media(path: string) {
    return URL.createObjectURL(await firstValueFrom(this.http.get("/api" + path, { responseType: "blob" })));
  }
  async download(data: {
    contentBase64?: string;
    base64String?: string;
    base64?: string;
    fileName?: string;
    file_name?: string;
    mimeType?: string;
    mime_type?: string;
  }) {
    const bytes = Uint8Array.from(atob(data.contentBase64 || data.base64String || data.base64 || ""), (c) =>
      c.charCodeAt(0),
    );
    const url = URL.createObjectURL(
      new Blob([bytes], { type: data.mimeType || data.mime_type || "application/octet-stream" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = data.fileName || data.file_name || "report";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
export function errorMessage(error: any): string {
  return (
    error?.error?.message ||
    error?.error?.detail ||
    error?.error?.error_description ||
    error?.error?.error ||
    error?.message ||
    "Could not complete the request. Please try again."
  );
}
