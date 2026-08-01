alter table public.render_assets
    drop constraint if exists render_assets_image_tag_check;

alter table public.render_assets
    add constraint render_assets_omni_tag_check
    check (
        image_tag is null
        or image_tag ~ '^@(image[1-9]|video[1-3]|audio[1-3])$'
    );

comment on column public.render_assets.image_tag is
    'Legacy column name containing an omni asset tag: @imageN, @videoN or @audioN.';
