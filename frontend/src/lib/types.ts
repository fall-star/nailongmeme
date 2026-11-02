export type MemeItem = {
  id: string;
  name: string;
  tag: string[];
  format: 'gif' | 'jpeg' | string;
  width: number | null;
  height: number | null;
  bytes: number | null;
  image: string;
  download: string;
  thumb320: string;
  thumb640: string;
};

export type GalleryPayload = {
  total: number;
  limit: number;
  offset: number;
  items: MemeItem[];
};
